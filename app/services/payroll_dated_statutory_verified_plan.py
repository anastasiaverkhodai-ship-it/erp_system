"""Read-only, fail-closed verified dated statutory planning.

Not an authorization for posting. The multi-rate writer remains disabled.
"""

from decimal import Decimal

from sqlalchemy import select

from app.models.employment_contract import EmploymentContract
from app.models.payroll import PayrollCalculation
from app.models.payroll_statutory import PayrollStatutoryComponent
from app.services.payroll_dated_component_verified import (
    preview_verified_dated_salary_tax,
)
from app.services.payroll_dated_statutory_plan import (
    build_dated_statutory_plan,
)


class VerifiedDatedStatutoryPlanError(ValueError):
    pass


COMPONENTS = (
    PayrollStatutoryComponent.PERSONAL_INCOME_TAX,
    PayrollStatutoryComponent.MILITARY_LEVY,
    PayrollStatutoryComponent.UNIFIED_SOCIAL_CONTRIBUTION,
)


async def build_verified_dated_salary_statutory_plan(
    db, *, company_id, payroll_calculation_id
):
    """Prove all three components before constructing any DB result.

    The simple plan explicitly excludes employee-wide multi-contract
    allocation and special tax profiles/base rules. Those require
    separate proof and cannot be silently treated as plain income.
    """
    for label, value in (
        ("company", company_id),
        ("payroll calculation", payroll_calculation_id),
    ):
        if type(value) is not int or value <= 0:
            raise VerifiedDatedStatutoryPlanError(
                f"Invalid {label} identifier"
            )

    calculation = await db.scalar(
        select(PayrollCalculation).where(
            PayrollCalculation.company_id == company_id,
            PayrollCalculation.id == payroll_calculation_id,
        )
    )
    if calculation is None:
        raise VerifiedDatedStatutoryPlanError(
            "Payroll calculation not found in company"
        )

    if calculation.currency_code != "UAH":
        raise VerifiedDatedStatutoryPlanError(
            "Dated statutory plan requires UAH"
        )

    contract = await db.scalar(
        select(EmploymentContract).where(
            EmploymentContract.company_id == company_id,
            EmploymentContract.id == calculation.employment_contract_id,
        )
    )
    if contract is None:
        raise VerifiedDatedStatutoryPlanError(
            "Employment contract not found in company"
        )

    from app.services.payroll_revision_service import current_calculation_filter
    # A peer payroll calculation for the same employee/month requires
    # the full employee-wide allocation engine.
    peers = (
        await db.scalars(
            select(PayrollCalculation.id)
            .join(
                EmploymentContract,
                EmploymentContract.id
                == PayrollCalculation.employment_contract_id,
            )
            .where(
                PayrollCalculation.company_id == company_id,
                EmploymentContract.company_id == company_id,
                EmploymentContract.employee_id == contract.employee_id,
                PayrollCalculation.payroll_period_id
                == calculation.payroll_period_id,
                PayrollCalculation.id != calculation.id,
                current_calculation_filter(),
            )
            .limit(1)
        )
    ).all()

    if peers:
        raise VerifiedDatedStatutoryPlanError(
            "Multiple employee calculations require "
            "employee-wide dated reconciliation"
        )

    component_amounts = {}

    for component in COMPONENTS:
        # Independently authenticate salary sources and every rate day.
        # This rejects special policies and unsupported source types.
        component_amounts[component.value] = tuple(
            await preview_verified_dated_salary_tax(
                db,
                company_id=company_id,
                payroll_calculation_id=payroll_calculation_id,
                component=component,
            )
        )

    # This validates three-component reconciliation and net amount.
    # It returns a plan only; no header, line, GL or payment is saved.
    return build_dated_statutory_plan(
        gross_amount=Decimal(calculation.gross_amount),
        currency_code=calculation.currency_code,
        components=component_amounts,
    )
