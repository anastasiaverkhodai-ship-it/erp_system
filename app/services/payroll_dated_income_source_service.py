"""Read-only, fail-closed dated salary-to-tax policy attribution.

This is a source verification adapter, not a statutory calculation
or authorization to post payments. Unsupported income classifications
must remain blocked until their dated provenance is implemented.
"""

from datetime import timedelta
from decimal import Decimal

from sqlalchemy import select

from app.models.payroll import (
    PayrollCalculation,
    PayrollCalculationLine,
    PayrollPeriod,
)
from app.models.employment_contract import EmploymentContract
from app.services.payroll_dated_income_allocation import (
    DatedIncome,
    TaxPolicyWindow,
    DatedIncomeAllocationError,
    allocate_verified_dated_income,
)
from app.services.payroll_statutory_service import (
    resolve_statutory_rate,
    resolve_payroll_employee_tax_profile,
    resolve_payroll_statutory_base_rule,
)


async def load_verified_dated_salary_allocation(
    db,
    *,
    company_id,
    payroll_calculation_id,
    component,
):
    """Resolve authoritative dated salary amounts against daily policies.

    Daily resolution locates policy boundaries; it never prorates income.
    Existing income lines must already be split where policy IDs change.
    """

    calculation = await db.scalar(
        select(PayrollCalculation).where(
            PayrollCalculation.company_id == company_id,
            PayrollCalculation.id == payroll_calculation_id,
        )
    )

    if calculation is None:
        raise DatedIncomeAllocationError(
            "Payroll calculation not found in company"
        )

    if calculation.currency_code != "UAH":
        raise DatedIncomeAllocationError(
            "Dated salary attribution supports UAH only"
        )

    period = await db.scalar(
        select(PayrollPeriod).where(
            PayrollPeriod.company_id == company_id,
            PayrollPeriod.id == calculation.payroll_period_id,
        )
    )

    contract = await db.scalar(
        select(EmploymentContract).where(
            EmploymentContract.company_id == company_id,
            EmploymentContract.id == calculation.employment_contract_id,
        )
    )

    if period is None or contract is None:
        raise DatedIncomeAllocationError(
            "Payroll period or employment contract is missing"
        )

    lines = list(
        (
            await db.scalars(
                select(PayrollCalculationLine).where(
                    PayrollCalculationLine.company_id == company_id,
                    PayrollCalculationLine.payroll_calculation_id
                    == payroll_calculation_id,
                ).order_by(
                    PayrollCalculationLine.line_no,
                    PayrollCalculationLine.id,
                )
            )
        ).all()
    )

    if not lines:
        raise DatedIncomeAllocationError(
            "Verified payroll calculation lines are required"
        )

    if any(line.line_type != "salary" for line in lines):
        raise DatedIncomeAllocationError(
            "Mixed income types require independently verified "
            "dated source classification"
        )

    income = []

    for line in lines:
        if (
            line.source_effective_from is None
            or line.source_effective_to is None
            or line.source_salary_rate_id is None
        ):
            raise DatedIncomeAllocationError(
                "Salary line lacks authoritative dated source provenance"
            )

        if not (
            period.start_date
            <= line.source_effective_from
            <= line.source_effective_to
            <= period.end_date
        ):
            raise DatedIncomeAllocationError(
                "Salary source dates fall outside payroll period"
            )

        if (
            line.source_effective_from < contract.start_date
            or (
                contract.end_date is not None
                and line.source_effective_to > contract.end_date
            )
        ):
            raise DatedIncomeAllocationError(
                "Salary source dates fall outside employment contract"
            )

        income.append(
            DatedIncome(
                source_line_id=line.id,
                effective_from=line.source_effective_from,
                effective_to=line.source_effective_to,
                amount=Decimal(line.amount),
            )
        )

    source_total = sum(
        (line.amount for line in income),
        Decimal("0.00"),
    )

    if source_total != Decimal(calculation.gross_amount):
        raise DatedIncomeAllocationError(
            "Dated salary lines do not reconcile to payroll gross"
        )

    first_day = min(item.effective_from for item in income)
    final_day = max(item.effective_to for item in income)

    windows = []
    current = first_day
    window_start = first_day
    previous_ids = None

    while current <= final_day:
        rate = await resolve_statutory_rate(
            db,
            company_id=company_id,
            component=component,
            effective_date=current,
            employee_id=contract.employee_id,
        )

        profile = await resolve_payroll_employee_tax_profile(
            db,
            company_id=company_id,
            employee_id=contract.employee_id,
            employment_contract_id=contract.id,
            effective_date=current,
        )

        rule = await resolve_payroll_statutory_base_rule(
            db,
            company_id=company_id,
            component=component,
            employment_kind=contract.employment_kind,
            tax_profile_category=(
                profile.category if profile is not None else None
            ),
            effective_date=current,
        )

        ids = (
            rate.id,
            profile.id if profile is not None else None,
            rule.id if rule is not None else None,
        )

        if previous_ids is not None and ids != previous_ids:
            windows.append(
                TaxPolicyWindow(
                    effective_from=window_start,
                    effective_to=current - timedelta(days=1),
                    source_rate_id=previous_ids[0],
                    source_profile_id=previous_ids[1],
                    source_base_rule_id=previous_ids[2],
                )
            )
            window_start = current

        previous_ids = ids
        current += timedelta(days=1)

    windows.append(
        TaxPolicyWindow(
            effective_from=window_start,
            effective_to=final_day,
            source_rate_id=previous_ids[0],
            source_profile_id=previous_ids[1],
            source_base_rule_id=previous_ids[2],
        )
    )

    return allocate_verified_dated_income(
        income_lines=income,
        policy_windows=windows,
    )
