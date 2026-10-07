from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.payroll import PayrollCalculation, PayrollPeriod
from app.models.payroll_regulatory_report import (
    PayrollRegulatoryReport,
    PayrollRegulatoryReportKind,
    PayrollRegulatoryReportRow,
    PayrollRegulatoryReportStatus,
)


class PayrollRegulatoryReportError(ValueError):
    pass


class PayrollRegulatoryReportNotFoundError(
    PayrollRegulatoryReportError
):
    pass


def _kind_value(
    report_kind: PayrollRegulatoryReportKind | str,
) -> str:
    if isinstance(report_kind, PayrollRegulatoryReportKind):
        return report_kind.value

    value = str(report_kind).lower()

    valid = {
        item.value
        for item in PayrollRegulatoryReportKind
    }
    if value not in valid:
        raise PayrollRegulatoryReportError(
            f"Unsupported payroll regulatory report kind: {value}"
        )

    return value


async def _get_period(
    db: AsyncSession,
    *,
    company_id: int,
    payroll_period_id: int,
) -> PayrollPeriod:
    period = await db.scalar(
        select(PayrollPeriod).where(
            PayrollPeriod.company_id == company_id,
            PayrollPeriod.id == payroll_period_id,
        )
    )

    if period is None:
        raise PayrollRegulatoryReportNotFoundError(
            "Payroll period not found"
        )

    return period


async def create_payroll_regulatory_report(
    db: AsyncSession,
    *,
    company_id: int,
    payroll_period_id: int,
    report_kind: PayrollRegulatoryReportKind | str,
    created_by: int,
) -> PayrollRegulatoryReport:
    await _get_period(
        db,
        company_id=company_id,
        payroll_period_id=payroll_period_id,
    )

    kind = _kind_value(report_kind)

    max_revision = await db.scalar(
        select(
            func.coalesce(
                func.max(PayrollRegulatoryReport.revision),
                0,
            )
        ).where(
            PayrollRegulatoryReport.company_id == company_id,
            PayrollRegulatoryReport.payroll_period_id
            == payroll_period_id,
            PayrollRegulatoryReport.report_kind == kind,
        )
    )

    report = PayrollRegulatoryReport(
        company_id=company_id,
        payroll_period_id=payroll_period_id,
        report_kind=kind,
        revision=int(max_revision or 0) + 1,
        status=PayrollRegulatoryReportStatus.DRAFT.value,
        created_by=created_by,
    )

    db.add(report)
    await db.flush()
    await db.refresh(report)

    return report


async def generate_payroll_regulatory_report(
    db: AsyncSession,
    *,
    company_id: int,
    payroll_regulatory_report_id: int,
    generated_by: int,
) -> PayrollRegulatoryReport:
    report = await db.scalar(
        select(PayrollRegulatoryReport)
        .where(
            PayrollRegulatoryReport.company_id == company_id,
            PayrollRegulatoryReport.id
            == payroll_regulatory_report_id,
        )
        .with_for_update()
    )

    if report is None:
        raise PayrollRegulatoryReportNotFoundError(
            "Payroll regulatory report not found"
        )

    if report.status == PayrollRegulatoryReportStatus.GENERATED.value:
        return report

    from app.services.payroll_revision_service import current_calculation_filter
    calculations = (
        await db.scalars(
            select(PayrollCalculation)
            .where(
                PayrollCalculation.company_id == company_id,
                PayrollCalculation.payroll_period_id
                == report.payroll_period_id,
                current_calculation_filter(),
            )
            .order_by(
                PayrollCalculation.employment_contract_id,
                PayrollCalculation.revision,
                PayrollCalculation.id,
            )
        )
    ).all()

    existing_rows = (
        await db.scalars(
            select(PayrollRegulatoryReportRow).where(
                PayrollRegulatoryReportRow.company_id == company_id,
                PayrollRegulatoryReportRow.payroll_regulatory_report_id
                == report.id,
            )
        )
    ).all()

    if existing_rows:
        raise PayrollRegulatoryReportError(
            "Draft payroll regulatory report already contains rows"
        )

    for row_no, calculation in enumerate(calculations, start=1):
        payload = {
            "report_kind": report.report_kind,
            "payroll_period_id": report.payroll_period_id,
            "payroll_calculation_id": calculation.id,
            "employment_contract_id":
                calculation.employment_contract_id,
            "revision": calculation.revision,
        }

        db.add(
            PayrollRegulatoryReportRow(
                company_id=company_id,
                payroll_regulatory_report_id=report.id,
                row_no=row_no,
                employee_id=None,
                employment_contract_id=
                    calculation.employment_contract_id,
                payroll_calculation_id=calculation.id,
                row_code=report.report_kind,
                payload_json=json.dumps(
                    payload,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            )
        )

    report.status = PayrollRegulatoryReportStatus.GENERATED.value
    report.generated_at = datetime.now(timezone.utc)
    report.generated_by = generated_by

    await db.flush()
    await db.refresh(report)

    return report


async def list_payroll_regulatory_report_rows(
    db: AsyncSession,
    *,
    company_id: int,
    payroll_regulatory_report_id: int,
) -> list[PayrollRegulatoryReportRow]:
    report = await db.scalar(
        select(PayrollRegulatoryReport.id).where(
            PayrollRegulatoryReport.company_id == company_id,
            PayrollRegulatoryReport.id
            == payroll_regulatory_report_id,
        )
    )

    if report is None:
        raise PayrollRegulatoryReportNotFoundError(
            "Payroll regulatory report not found"
        )

    return list(
        (
            await db.scalars(
                select(PayrollRegulatoryReportRow)
                .where(
                    PayrollRegulatoryReportRow.company_id
                    == company_id,
                    PayrollRegulatoryReportRow.payroll_regulatory_report_id
                    == payroll_regulatory_report_id,
                )
                .order_by(PayrollRegulatoryReportRow.row_no)
            )
        ).all()
    )
