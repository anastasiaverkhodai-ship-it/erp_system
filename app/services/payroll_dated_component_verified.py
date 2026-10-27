"""Database-verified preview of dated ordinary salary taxes.

Read-only. Does not authorize statutory postings, special profiles,
USC floors/ceilings, social benefits or multi-contract allocation.
"""

from decimal import Decimal

from sqlalchemy import select

from app.models.payroll_statutory import PayrollStatutoryRate
from app.services.payroll_dated_income_allocation import (
    DatedIncomeAllocationError,
)
from app.services.payroll_dated_income_source_service import (
    load_verified_dated_salary_allocation,
)
from app.services.payroll_dated_component_preview import (
    preview_dated_component,
    DatedComponentPreviewError,
)
from app.services.payroll_statutory_service import (
    resolve_statutory_rate,
)


class VerifiedDatedComponentError(ValueError):
    pass


async def preview_verified_dated_salary_tax(
    db,
    *,
    company_id,
    payroll_calculation_id,
    component,
):
    """Read authentic salary lines and authenticate each rate with DB.

    Every day covered by a source line must resolve to the identical
    statutory rate ID, with valid existing evidence checks.
    """

    if isinstance(company_id, bool) or not isinstance(company_id, int) or company_id <= 0:
        raise VerifiedDatedComponentError(
            "Invalid company identifier"
        )

    if (
        isinstance(payroll_calculation_id, bool)
        or not isinstance(payroll_calculation_id, int)
        or payroll_calculation_id <= 0
    ):
        raise VerifiedDatedComponentError(
            "Invalid payroll calculation identifier"
        )

    try:
        allocations = await load_verified_dated_salary_allocation(
            db,
            company_id=company_id,
            payroll_calculation_id=payroll_calculation_id,
            component=component,
        )

        if not allocations:
            raise VerifiedDatedComponentError(
                "Verified dated income is empty"
            )

        # These classifications require the existing employee-wide
        # tax engine, rather than the simple dated arithmetic preview.
        if any(
            row.source_profile_id is not None
            or row.source_base_rule_id is not None
            for row in allocations
        ):
            raise VerifiedDatedComponentError(
                "Special tax profiles or base rules require "
                "the full statutory calculation engine"
            )

        ids = {row.source_rate_id for row in allocations}

        rates = list(
            (
                await db.scalars(
                    select(PayrollStatutoryRate).where(
                        PayrollStatutoryRate.company_id == company_id,
                        PayrollStatutoryRate.id.in_(ids),
                        PayrollStatutoryRate.component == component.value,
                    )
                )
            ).all()
        )

        by_id = {row.id: row for row in rates}

        if set(by_id) != ids:
            raise VerifiedDatedComponentError(
                "Statutory rate is missing or belongs "
                "to another company or component"
            )

        from datetime import timedelta

        for item in allocations:
            day = item.effective_from

            while day <= item.effective_to:
                resolved = await resolve_statutory_rate(
                    db,
                    company_id=company_id,
                    component=component,
                    effective_date=day,
                    employee_id=await _employee_id(
                        db,
                        company_id=company_id,
                        payroll_calculation_id=payroll_calculation_id,
                    ),
                )

                if resolved.id != item.source_rate_id:
                    raise VerifiedDatedComponentError(
                        "Resolved tax rate differs from the "
                        "verified dated income source"
                    )

                day += timedelta(days=1)

        verified_rates = {
            rate_id: Decimal(by_id[rate_id].rate)
            for rate_id in ids
        }

        gross = sum(
            (item.amount for item in allocations),
            Decimal("0.00"),
        )

        return preview_dated_component(
            allocations=tuple(allocations),
            verified_rates=verified_rates,
            expected_gross=gross,
        )

    except (DatedIncomeAllocationError, DatedComponentPreviewError) as exc:
        raise VerifiedDatedComponentError(str(exc)) from exc


async def _employee_id(
    db,
    *,
    company_id,
    payroll_calculation_id,
):
    from app.models.payroll import PayrollCalculation
    from app.models.employment_contract import EmploymentContract

    result = await db.scalar(
        select(EmploymentContract.employee_id)
        .join(
            PayrollCalculation,
            PayrollCalculation.employment_contract_id
            == EmploymentContract.id,
        )
        .where(
            PayrollCalculation.company_id == company_id,
            EmploymentContract.company_id == company_id,
            PayrollCalculation.id == payroll_calculation_id,
        )
    )

    if result is None:
        raise VerifiedDatedComponentError(
            "Verified employment contract was not found"
        )

    return result
