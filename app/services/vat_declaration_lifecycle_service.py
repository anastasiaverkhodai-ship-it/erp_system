from __future__ import annotations

from datetime import date

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.vat_declaration import VatDeclaration
from app.models.vat_declaration_status_event import (
    VatDeclarationStatusEvent,
)


_ALLOWED_TRANSITIONS = {
    "prepared": {"finalized"},
    "finalized": {"submitted"},
    "submitted": {"accepted", "rejected"},
    "accepted": set(),
    "rejected": set(),
}


def _fail(detail: str) -> None:
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=detail,
    )


async def append_vat_declaration_status(
    *,
    db: AsyncSession,
    company_id: int,
    vat_declaration_id: int,
    new_status: str,
    event_date: date,
    created_by: int,
    reference: str | None = None,
) -> VatDeclarationStatusEvent:
    from app.models.company import Company
    if company_id<=0 or created_by<=0:
        _fail("Invalid declaration actor/company")
    await db.scalar(select(Company.id).where(Company.id==company_id).with_for_update())
    declaration = (
        await db.execute(
            select(VatDeclaration)
            .where(
                VatDeclaration.company_id == company_id,
                VatDeclaration.id == vat_declaration_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()

    if declaration is None:
        _fail("VAT declaration not found")

    latest = (
        await db.execute(
            select(VatDeclarationStatusEvent)
            .where(
                VatDeclarationStatusEvent.company_id
                == company_id,
                VatDeclarationStatusEvent.vat_declaration_id
                == vat_declaration_id,
            )
            .order_by(
                VatDeclarationStatusEvent.created_at.desc(),
                VatDeclarationStatusEvent.id.desc(),
            )
            .limit(1)
            .with_for_update()
        )
    ).scalar_one_or_none()

    if latest is None:
        _fail("VAT declaration has no lifecycle history")

    normalized_reference=reference.strip() if reference else None
    if normalized_reference and len(normalized_reference)>500:
        _fail("Declaration reference exceeds 500 characters")
    if event_date>date.today() or event_date<latest.event_date:
        _fail("Declaration status date is future or precedes lifecycle history")
    if (latest.status,latest.event_date,latest.reference)==(new_status,event_date,normalized_reference):
        return latest
    newest=await db.scalar(select(VatDeclaration.id).where(VatDeclaration.company_id==company_id,
        VatDeclaration.reporting_year==declaration.reporting_year,VatDeclaration.reporting_month==declaration.reporting_month)
        .order_by(VatDeclaration.snapshot_version.desc()).limit(1))
    if newest!=declaration.id:
        _fail("Superseded declaration cannot change status")
    if new_status=='finalized':
        if event_date<=declaration.period_end:
            _fail("Reporting month must end before finalization")
        from datetime import datetime, timezone
        from app.services.vat_register_service import get_vat_register
        control=await get_vat_register(db,company_id=company_id,date_from=declaration.period_start,
            date_to=declaration.period_end,as_of=declaration.source_cutoff_at,declaration_id=declaration.id)
        issues=[i for i in control['issues'] if i['code'] not in ('pending_output_vat_decrease','unclassified_tax_settlement_journal')]
        if issues:
            _fail("Declaration cannot be finalized: " + ', '.join(sorted({i['code'] for i in issues})))
        from app.services.vat_declaration_source_loader_service import load_vat_declaration_sources
        from app.models.vat_declaration_source_line import VatDeclarationSourceLine
        from app.services.vat_declaration_persistence_service import _source_fk_kwargs
        fresh=await load_vat_declaration_sources(db=db,company_id=company_id,period_start=declaration.period_start,
            period_end=declaration.period_end,source_cutoff_at=datetime.now(timezone.utc))
        stored=list((await db.scalars(select(VatDeclarationSourceLine).where(
            VatDeclarationSourceLine.company_id==company_id,VatDeclarationSourceLine.vat_declaration_id==declaration.id))).all())
        def key(s):
            return (s.source_kind,s.economic_effective_date,s.direction,s.taxable_base_delta,s.tax_amount_delta,s.tax_rate_code,s.tax_rate)
        expected=sorted([(key(s),next(v for v in _source_fk_kwargs(s).values() if v is not None)) for s in fresh])
        actual=sorted([(key(s),next(getattr(s,k) for k in _source_fk_kwargs(fresh[0]) if getattr(s,k) is not None)) for s in stored]) if stored and fresh else []
        if expected!=actual or len(stored)!=len(fresh):
            _fail("Declaration snapshot is stale; rebuild before finalizing")
    allowed = _ALLOWED_TRANSITIONS.get(
        latest.status
    )

    if allowed is None or new_status not in allowed:
        _fail(
            "Illegal VAT declaration lifecycle transition"
        )

    normalized_reference = (
        reference.strip()
        if reference is not None
        else None
    )

    if new_status in {
        "submitted",
        "accepted",
        "rejected",
    } and not normalized_reference:
        _fail(
            "VAT declaration lifecycle reference is required"
        )

    event = VatDeclarationStatusEvent(
        company_id=company_id,
        vat_declaration_id=vat_declaration_id,
        status=new_status,
        event_date=event_date,
        reference=normalized_reference,
        created_by=created_by,
    )

    db.add(event)
    await db.flush()

    return event
