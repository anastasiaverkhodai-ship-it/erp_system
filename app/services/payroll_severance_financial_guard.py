"""Fail-closed severance classification boundary for financial writes."""

from sqlalchemy import select

from app.models.payroll import PayrollCalculationLine


async def require_severance_financial_classification(
    db,
    *,
    company_id: int,
    payroll_calculation_id: int,
    error_type,
    consumer=None,
):
    """Reject financial use of an unclassified severance calculation.

    Each integrated consumer must revalidate the immutable source and policy.
    """
    severance_id = await db.scalar(
        select(PayrollCalculationLine.id)
        .where(
            PayrollCalculationLine.company_id == company_id,
            PayrollCalculationLine.payroll_calculation_id
            == payroll_calculation_id,
            PayrollCalculationLine.line_type == "severance",
        )
        .limit(1)
    )

    if severance_id is not None:
        if consumer in {"statutory", "accounting", "payslip", "payment", "deductions"}:
            from app.services.payroll_severance_trusted_classification import require_trusted_severance_snapshot
            try:
                return await require_trusted_severance_snapshot(
                    db, company_id=company_id, payroll_calculation_id=payroll_calculation_id,
                )
            except ValueError as exc:
                raise error_type(f"Severance statutory classification: {exc}") from exc
        raise error_type(
            "Severance statutory classification is required "
            "before tax, deductions, payslip and accounting"
        )
