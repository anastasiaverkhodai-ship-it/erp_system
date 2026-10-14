"""Read-only historical salary-rate proof for payroll corrections.

This proves a limited relationship between salary rates, input slices
and salary calculation lines. It does NOT authorize corrections.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import select

from app.models.employee_salary_rate import EmployeeSalaryRate
from app.models.payroll import (
    PayrollInputSalarySlice,
    PayrollCalculationLine,
)


class PayrollHistoricalSalaryProofError(ValueError):
    pass


def _decimal(value: Any) -> Decimal:
    try:
        result = Decimal(str(value))
    except (TypeError, ValueError, ArithmeticError) as exc:
        raise PayrollHistoricalSalaryProofError(
            "Invalid salary-rate amount"
        ) from exc

    if not result.is_finite():
        raise PayrollHistoricalSalaryProofError(
            "Non-finite salary-rate amount"
        )

    return result


def verify_salary_rate_slice(rate, salary_slice, *,
                             company_id: int,
                             employment_contract_id: int) -> None:
    """Raise unless the historical rate matches its salary slice."""

    if rate is None:
        raise PayrollHistoricalSalaryProofError(
            "Referenced historical salary rate is missing"
        )

    if salary_slice is None:
        raise PayrollHistoricalSalaryProofError(
            "Salary slice is missing"
        )

    if (
        rate.company_id != company_id
        or rate.employment_contract_id != employment_contract_id
        or salary_slice.company_id != company_id
    ):
        raise PayrollHistoricalSalaryProofError(
            "Historical salary rate ownership mismatch"
        )

    if salary_slice.salary_rate_id != rate.id:
        raise PayrollHistoricalSalaryProofError(
            "Historical salary rate reference mismatch"
        )

    if (
        rate.rate_type != salary_slice.salary_rate_type
        or rate.currency_code != salary_slice.currency_code
        or _decimal(rate.amount)
        != _decimal(salary_slice.salary_rate_amount)
    ):
        raise PayrollHistoricalSalaryProofError(
            "Historical salary rate terms mismatch"
        )

    start = salary_slice.effective_from
    end = salary_slice.effective_to

    if start is None or end is None or start > end:
        raise PayrollHistoricalSalaryProofError(
            "Invalid salary slice effective dates"
        )

    if (
        start < rate.effective_from
        or (
            rate.effective_to is not None
            and end > rate.effective_to
        )
    ):
        raise PayrollHistoricalSalaryProofError(
            "Salary slice extends beyond historical rate"
        )


async def verify_historical_salary_sources(
    db,
    *,
    company_id: int,
    employment_contract_id: int,
    payroll_input_id: int,
    payroll_calculation_id: int,
) -> dict:
    """Read and validate rate references without database mutation."""

    slices = (
        await db.scalars(
            select(PayrollInputSalarySlice)
            .where(
                PayrollInputSalarySlice.company_id == company_id,
                PayrollInputSalarySlice.payroll_input_id
                == payroll_input_id,
            )
            .order_by(PayrollInputSalarySlice.id)
        )
    ).all()

    lines = (
        await db.scalars(
            select(PayrollCalculationLine)
            .where(
                PayrollCalculationLine.company_id == company_id,
                PayrollCalculationLine.payroll_calculation_id
                == payroll_calculation_id,
            )
            .order_by(PayrollCalculationLine.line_no)
        )
    ).all()

    if not slices or not lines:
        raise PayrollHistoricalSalaryProofError(
            "Salary slices and calculation lines are required"
        )

    rate_ids = set()
    slices_by_rate = {}

    for salary_slice in slices:
        rate = await db.scalar(
            select(EmployeeSalaryRate).where(
                EmployeeSalaryRate.id
                == salary_slice.salary_rate_id,
                EmployeeSalaryRate.company_id == company_id,
                EmployeeSalaryRate.employment_contract_id
                == employment_contract_id,
            )
        )

        verify_salary_rate_slice(
            rate,
            salary_slice,
            company_id=company_id,
            employment_contract_id=employment_contract_id,
        )

        rate_ids.add(rate.id)
        slices_by_rate.setdefault(rate.id, []).append(
            salary_slice
        )

    salary_line_count = 0

    for line in lines:
        if str(line.line_type).lower() != "salary":
            continue

        salary_line_count += 1

        if (
            line.source_salary_rate_id is None
            or line.source_salary_rate_id not in rate_ids
        ):
            raise PayrollHistoricalSalaryProofError(
                "Salary calculation line has no verified rate"
            )

        if not any(
            line.currency_code == item.currency_code
            and line.salary_rate_type == item.salary_rate_type
            and line.source_effective_from == item.effective_from
            and line.source_effective_to == item.effective_to
            for item in slices_by_rate[
                line.source_salary_rate_id
            ]
        ):
            raise PayrollHistoricalSalaryProofError(
                "Salary calculation line provenance mismatch"
            )

    if salary_line_count == 0:
        raise PayrollHistoricalSalaryProofError(
            "No salary calculation lines found"
        )

    return {
        "schema": "payroll-historical-salary-proof-v1",
        "salary_slice_count": len(slices),
        "salary_line_count": salary_line_count,
        "historical_salary_rate_count": len(rate_ids),
        "historical_salary_rate_references_verified": True,
        "complete_payroll_provenance_verified": False,
        "automatic_revision_creation_allowed": False,
        "financial_execution_allowed": False,
    }
