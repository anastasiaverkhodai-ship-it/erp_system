"""Fail-closed severance classification boundary for financial writes."""

from sqlalchemy import select

from app.models.payroll import PayrollCalculationLine


async def require_severance_financial_classification(
    db,
    *,
    company_id: int,
    payroll_calculation_id: int,
    error_type,
) -> None:
    """Reject financial use of an unclassified severance calculation.

    Until a dated statutory classification matrix is implemented,
    ALL severance lines are blocked. No tax-rate inference is allowed.
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
        raise error_type(
            "Severance statutory classification is required "
            "before tax, deductions, payslip and accounting"
        )
