from __future__ import annotations

from calendar import monthrange
from datetime import date, datetime, timezone, timedelta

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.accounting_period import AccountingPeriod
from app.models.vat_declaration import VatDeclaration
from app.models.vat_declaration_carry_forward_line import (
    VatDeclarationCarryForwardLine,
)
from app.models.vat_declaration_source_line import (
    VatDeclarationSourceLine,
)
from app.models.vat_declaration_status_event import (
    VatDeclarationStatusEvent,
)
from app.services.vat_declaration_aggregation_service import (
    aggregate_vat_declaration_sources,
)
from app.services.vat_declaration_carry_forward_service import (
    OpeningCarryTranche,
    apply_fifo_negative_carry,
)
from app.services.vat_declaration_source_loader_service import (
    VatDeclarationSource,
    load_vat_declaration_sources,
)


def _fail(detail: str) -> None:
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=detail,
    )


def _source_fk_kwargs(
    source: VatDeclarationSource,
) -> dict[str, int | None]:
    values: dict[str, int | None] = {
        "tax_recognition_event_id": None,
        "sales_return_recognition_event_id": None,
        "trade_value_correction_event_id": None,
        (
            "purchase_return_input_vat_credit_"
            "correction_event_id"
        ): None,
        (
            "purchase_value_correction_input_vat_credit_"
            "correction_event_id"
        ): None,
    }

    if source.source_kind in {
        "output_tax_recognition",
        "input_tax_recognition",
    }:
        values["tax_recognition_event_id"] = source.source_id
    elif source.source_kind == "sales_return":
        values[
            "sales_return_recognition_event_id"
        ] = source.source_id
    elif source.source_kind == "sales_value_correction":
        values[
            "trade_value_correction_event_id"
        ] = source.source_id
    elif (
        source.source_kind
        == "purchase_return_input_credit_correction"
    ):
        values[
            "purchase_return_input_vat_credit_correction_event_id"
        ] = source.source_id
    elif (
        source.source_kind
        == "purchase_value_input_credit_correction"
    ):
        values[
            "purchase_value_correction_input_vat_credit_correction_event_id"
        ] = source.source_id
    else:
        _fail("Unsupported VAT declaration source kind")

    return values


async def _lock_accounting_period(
    *,
    db: AsyncSession,
    company_id: int,
    reporting_year: int,
    reporting_month: int,
) -> AccountingPeriod:
    period = (
        await db.execute(
            select(AccountingPeriod)
            .where(
                AccountingPeriod.company_id == company_id,
                AccountingPeriod.year == reporting_year,
                AccountingPeriod.month == reporting_month,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()

    if period is None:
        _fail("Accounting period does not exist")

    return period


async def _latest_period_snapshot(
    *,
    db: AsyncSession,
    company_id: int,
    reporting_year: int,
    reporting_month: int,
    lock: bool,
) -> VatDeclaration | None:
    statement = (
        select(VatDeclaration)
        .where(
            VatDeclaration.company_id == company_id,
            VatDeclaration.reporting_year == reporting_year,
            VatDeclaration.reporting_month == reporting_month,
        )
        .order_by(
            VatDeclaration.snapshot_version.desc(),
            VatDeclaration.id.desc(),
        )
        .limit(1)
    )

    if lock:
        statement = statement.with_for_update()

    return (
        await db.execute(statement)
    ).scalar_one_or_none()


async def _latest_prior_snapshot(
    *,
    db: AsyncSession,
    company_id: int,
    period_start: date,
) -> VatDeclaration | None:
    return (
        await db.execute(
            select(VatDeclaration)
            .where(
                VatDeclaration.company_id == company_id,
                VatDeclaration.period_end < period_start,
            )
            .order_by(
                VatDeclaration.period_end.desc(),
                VatDeclaration.snapshot_version.desc(),
                VatDeclaration.id.desc(),
            )
            .limit(1)
        )
    ).scalar_one_or_none()


async def _opening_carry_from_predecessor(
    *,
    db: AsyncSession,
    company_id: int,
    predecessor: VatDeclaration | None,
) -> list[OpeningCarryTranche]:
    if predecessor is None:
        return []

    if predecessor.company_id != company_id:
        _fail("Cross-company VAT carry predecessor")

    rows = (
        await db.execute(
            select(VatDeclarationCarryForwardLine)
            .where(
                VatDeclarationCarryForwardLine.company_id
                == company_id,
                VatDeclarationCarryForwardLine.vat_declaration_id
                == predecessor.id,
                VatDeclarationCarryForwardLine.closing_amount > 0,
            )
            .order_by(
                VatDeclarationCarryForwardLine.origin_reporting_year,
                VatDeclarationCarryForwardLine.origin_reporting_month,
                VatDeclarationCarryForwardLine.id,
            )
        )
    ).scalars().all()

    tranches = [
        OpeningCarryTranche(
            source_declaration_id=predecessor.id,
            origin_reporting_year=row.origin_reporting_year,
            origin_reporting_month=row.origin_reporting_month,
            opening_amount=row.closing_amount,
        )
        for row in rows
    ]

    if predecessor.current_period_negative > 0:
        tranches.append(
            OpeningCarryTranche(
                source_declaration_id=predecessor.id,
                origin_reporting_year=predecessor.reporting_year,
                origin_reporting_month=predecessor.reporting_month,
                opening_amount=predecessor.current_period_negative,
            )
        )

    return tranches


async def build_vat_declaration_snapshot(
    *,
    db: AsyncSession,
    company_id: int,
    reporting_year: int,
    reporting_month: int,
    source_cutoff_at: datetime,
    created_by: int,
) -> VatDeclaration:
    if company_id <= 0 or created_by <= 0:
        _fail("Invalid VAT declaration actor/company")

    if reporting_month < 1 or reporting_month > 12:
        _fail("Invalid VAT declaration month")

    if not 2000 <= reporting_year <= 9999:
        _fail("Invalid VAT declaration year")
    if source_cutoff_at.tzinfo is None or source_cutoff_at.utcoffset() is None or source_cutoff_at > datetime.now(timezone.utc):
        _fail("VAT cutoff must be timezone-aware and not in the future")
    from app.models.company import Company
    # Serialize the complete carry chain, not only versions within one month.
    if await db.scalar(select(Company.id).where(Company.id==company_id).with_for_update()) is None:
        _fail("Company not found")
    period_start = date(
        reporting_year,
        reporting_month,
        1,
    )
    period_end = date(
        reporting_year,
        reporting_month,
        monthrange(
            reporting_year,
            reporting_month,
        )[1],
    )

    if period_start > date.today():
        _fail("Future VAT reporting period is not supported")
    if source_cutoff_at.date() < period_start:
        _fail("VAT cutoff predates reporting period")
    if await db.scalar(select(VatDeclaration.id).where(VatDeclaration.company_id==company_id,
            VatDeclaration.period_start>period_start).limit(1)):
        _fail("Later declaration snapshots exist; rebuild of an earlier period requires an amendment workflow")
    period = await _lock_accounting_period(
        db=db,
        company_id=company_id,
        reporting_year=reporting_year,
        reporting_month=reporting_month,
    )

    if (
        period.start_date != period_start
        or period.end_date != period_end
    ):
        _fail(
            "Accounting period does not match canonical calendar month"
        )

    latest = await _latest_period_snapshot(
        db=db,
        company_id=company_id,
        reporting_year=reporting_year,
        reporting_month=reporting_month,
        lock=True,
    )

    if latest is not None:
        latest_status=await db.scalar(select(VatDeclarationStatusEvent.status).where(
            VatDeclarationStatusEvent.company_id==company_id,
            VatDeclarationStatusEvent.vat_declaration_id==latest.id).order_by(VatDeclarationStatusEvent.id.desc()).limit(1))
        if latest_status not in ('prepared','rejected'):
            _fail("Finalized or submitted declaration cannot be replaced by a draft")

    version = (
        1
        if latest is None
        else latest.snapshot_version + 1
    )

    sources = await load_vat_declaration_sources(
        db=db,
        company_id=company_id,
        period_start=period_start,
        period_end=period_end,
        source_cutoff_at=source_cutoff_at,
    )

    totals = aggregate_vat_declaration_sources(
        sources
    )

    predecessor = await _latest_prior_snapshot(
        db=db,
        company_id=company_id,
        period_start=period_start,
    )

    if predecessor is not None:
        if predecessor.period_end != period_start-timedelta(days=1):
            _fail("Missing immediately preceding VAT declaration period")
        predecessor_status=await db.scalar(select(VatDeclarationStatusEvent.status).where(
            VatDeclarationStatusEvent.company_id==company_id,
            VatDeclarationStatusEvent.vat_declaration_id==predecessor.id,
            VatDeclarationStatusEvent.created_at<=source_cutoff_at).order_by(VatDeclarationStatusEvent.id.desc()).limit(1))
        if predecessor_status != 'accepted':
            _fail("Previous VAT declaration must be accepted at source cutoff")
    opening_tranches = await _opening_carry_from_predecessor(
        db=db,
        company_id=company_id,
        predecessor=predecessor,
    )

    carry = apply_fifo_negative_carry(
        output_vat=totals.output_vat,
        input_vat_credit=totals.input_vat_credit,
        opening_tranches=opening_tranches,
    )

    declaration = VatDeclaration(
        company_id=company_id,
        reporting_year=reporting_year,
        reporting_month=reporting_month,
        period_start=period_start,
        period_end=period_end,
        source_cutoff_at=source_cutoff_at,
        snapshot_version=version,
        supersedes_declaration_id=(
            latest.id if latest is not None else None
        ),
        output_taxable_base=totals.output_taxable_base,
        output_vat=totals.output_vat,
        input_taxable_base=totals.input_taxable_base,
        input_vat_credit=totals.input_vat_credit,
        opening_negative_carry=carry.opening_negative_carry,
        vat_payable=carry.vat_payable,
        current_period_negative=carry.current_period_negative,
        closing_negative_carry=carry.closing_negative_carry,
        currency_code="UAH",
        created_by=created_by,
    )

    db.add(declaration)
    await db.flush()

    for line_number, source in enumerate(
        sources,
        start=1,
    ):
        db.add(
            VatDeclarationSourceLine(
                company_id=company_id,
                vat_declaration_id=declaration.id,
                line_number=line_number,
                source_kind=source.source_kind,
                economic_effective_date=(
                    source.economic_effective_date
                ),
                direction=source.direction,
                taxable_base_delta=source.taxable_base_delta,
                tax_amount_delta=source.tax_amount_delta,
                currency_code="UAH",
                source_reversal_of_id=(
                    source.source_reversal_of_id
                ),
                tax_rate_code=source.tax_rate_code,
                tax_rate=source.tax_rate,
                **_source_fk_kwargs(source),
            )
        )

    for tranche in carry.tranches:
        db.add(
            VatDeclarationCarryForwardLine(
                company_id=company_id,
                vat_declaration_id=declaration.id,
                source_declaration_id=(
                    tranche.source_declaration_id
                ),
                origin_reporting_year=(
                    tranche.origin_reporting_year
                ),
                origin_reporting_month=(
                    tranche.origin_reporting_month
                ),
                opening_amount=tranche.opening_amount,
                consumed_amount=tranche.consumed_amount,
                closing_amount=tranche.closing_amount,
            )
        )

    db.add(
        VatDeclarationStatusEvent(
            company_id=company_id,
            vat_declaration_id=declaration.id,
            status="prepared",
            event_date=date.today(),
            reference=None,
            created_by=created_by,
        )
    )

    await db.flush()

    return declaration
