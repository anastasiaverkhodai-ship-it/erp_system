from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.services.payroll_mutation_guard import serialized_payroll_mutation

from app.models.payroll import PayrollPeriod
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


@serialized_payroll_mutation(PayrollRegulatoryReportError)
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


@serialized_payroll_mutation(PayrollRegulatoryReportError)
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

    # The former generator persisted identifiers only. Those rows, including
    # legacy GENERATED snapshots, must not be accepted as statutory reports.
    from app.services.payroll_regulatory_readiness_service import payroll_regulatory_readiness
    readiness = await payroll_regulatory_readiness(
        db, company_id=company_id, payroll_period_id=report.payroll_period_id,
        report_kind=report.report_kind,
    )
    raise PayrollRegulatoryReportError(
        "Regulatory report is incomplete: " + ", ".join(readiness['blocking_issues'])
    )


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
