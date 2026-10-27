"""Read-only evidence coordination for severance minimum-wage sources.

This is NOT financial authorization or Order 100 eligibility approval.
"""

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select

from app.models.payroll import (
    PayrollCalculation,
    PayrollCalculationLine,
    PayrollInput,
    PayrollPeriod,
)
from app.models.payroll import (
    PayrollInputSalarySlice,
)
from app.services.payroll_correction_historical_salary_proof import (
    verify_historical_salary_sources,
)
from app.services.payroll_correction_minimum_wage_rederivation import (
    verify_original_minimum_wage_rederivation,
)
from app.services.payroll_revision_service import (
    current_calculation_filter,
)


class SeveranceMinimumWageProvenanceError(ValueError):
    pass


@dataclass(frozen=True)
class VerifiedMinimumWageSource:
    company_id: int
    payroll_calculation_id: int
    revision: int
    historical_salary_verified: bool
    minimum_wage_rederived: bool
    complete_payroll_provenance_verified: bool
    financial_execution_allowed: bool


async def inspect_severance_minimum_wage_source(
    db,
    *,
    company_id: int,
    payroll_calculation_id: int,
) -> VerifiedMinimumWageSource:
    for value in (company_id, payroll_calculation_id):
        if type(value) is not int or value <= 0:
            raise SeveranceMinimumWageProvenanceError(
                "Valid company and calculation identifiers required"
            )

    calculation = await db.scalar(
        select(PayrollCalculation).where(
            PayrollCalculation.company_id == company_id,
            PayrollCalculation.id == payroll_calculation_id,
            current_calculation_filter(),
        )
    )

    if calculation is None:
        raise SeveranceMinimumWageProvenanceError(
            "Current company-scoped calculation required"
        )

    payroll_input = await db.scalar(
        select(PayrollInput).where(
            PayrollInput.company_id == company_id,
            PayrollInput.id == calculation.payroll_input_id,
            PayrollInput.employment_contract_id
            == calculation.employment_contract_id,
        )
    )
    period = await db.scalar(
        select(PayrollPeriod).where(
            PayrollPeriod.company_id == company_id,
            PayrollPeriod.id == calculation.payroll_period_id,
        )
    )

    if (
        payroll_input is None
        or period is None
        or payroll_input.payroll_period_id != period.id
    ):
        raise SeveranceMinimumWageProvenanceError(
            "Payroll source identity mismatch"
        )

    if getattr(period.status, "value", period.status) != "finalized":
        raise SeveranceMinimumWageProvenanceError(
            "Finalized source period required"
        )

    slices = list((
        await db.scalars(
            select(PayrollInputSalarySlice).where(
                PayrollInputSalarySlice.company_id == company_id,
                PayrollInputSalarySlice.payroll_input_id
                == payroll_input.id,
            ).order_by(PayrollInputSalarySlice.id)
        )
    ).all())

    lines = list((
        await db.scalars(
            select(PayrollCalculationLine).where(
                PayrollCalculationLine.company_id == company_id,
                PayrollCalculationLine.payroll_calculation_id
                == calculation.id,
            ).order_by(
                PayrollCalculationLine.line_no,
                PayrollCalculationLine.id,
            )
        )
    ).all())

    if not slices or not lines:
        raise SeveranceMinimumWageProvenanceError(
            "Historical salary evidence is incomplete"
        )

    salary_proof = await verify_historical_salary_sources(
        db,
        company_id=company_id,
        employment_contract_id=calculation.employment_contract_id,
        payroll_input_id=payroll_input.id,
        payroll_calculation_id=calculation.id,
    )

    wage_proof = await verify_original_minimum_wage_rederivation(
        db,
        company_id=company_id,
        period=period,
        payroll_input=payroll_input,
        salary_slices=slices,
        original_calculation=calculation,
        original_lines=lines,
    )

    historical_ok = (
        salary_proof.get(
            "historical_salary_rate_references_verified"
        ) is True
    )
    wage_ok = (
        wage_proof.get(
            "current_erp_rederivation_reconciled"
        ) is True
    )

    if not historical_ok or not wage_ok:
        raise SeveranceMinimumWageProvenanceError(
            "Minimum-wage source evidence not verified"
        )

    return VerifiedMinimumWageSource(
        company_id=company_id,
        payroll_calculation_id=calculation.id,
        revision=calculation.revision,
        historical_salary_verified=True,
        minimum_wage_rederived=True,
        complete_payroll_provenance_verified=False,
        financial_execution_allowed=False,
    )


@dataclass(frozen=True)
class VerifiedEarningsCandidate:
    payroll_calculation_id: int
    revision: int
    salary_amount: Decimal
    minimum_wage_amount: Decimal
    gross_amount: Decimal
    order100_inclusion_authorized: bool
    financial_execution_allowed: bool


async def derive_verified_earnings_candidate(
    db,
    *,
    company_id: int,
    payroll_calculation_id: int,
) -> VerifiedEarningsCandidate:
    """Reconcile current payroll earnings without authorizing severance."""
    proof = await inspect_severance_minimum_wage_source(
        db,
        company_id=company_id,
        payroll_calculation_id=payroll_calculation_id,
    )

    calculation = await db.scalar(
        select(PayrollCalculation).where(
            PayrollCalculation.company_id == company_id,
            PayrollCalculation.id == payroll_calculation_id,
            current_calculation_filter(),
        )
    )

    if calculation is None:
        raise SeveranceMinimumWageProvenanceError(
            "Current payroll source changed during verification"
        )

    lines = list((
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
    ).all())

    if not lines or any(
        line.line_type not in {"salary", "minimum_wage"}
        or line.currency_code != "UAH"
        for line in lines
    ):
        raise SeveranceMinimumWageProvenanceError(
            "Additional earnings require separate Order 100 proof"
        )

    salary = sum(
        (Decimal(line.amount) for line in lines
         if line.line_type == "salary"),
        Decimal("0"),
    )
    minimum = sum(
        (Decimal(line.amount) for line in lines
         if line.line_type == "minimum_wage"),
        Decimal("0"),
    )
    gross = Decimal(calculation.gross_amount)

    if (
        not salary.is_finite()
        or not minimum.is_finite()
        or not gross.is_finite()
        or salary <= 0
        or minimum < 0
        or salary + minimum != gross
        or calculation.currency_code != "UAH"
    ):
        raise SeveranceMinimumWageProvenanceError(
            "Salary and minimum wage fail gross reconciliation"
        )

    assessment = calculation.minimum_wage_assessment
    if not isinstance(assessment, dict):
        raise SeveranceMinimumWageProvenanceError(
            "Saved minimum wage assessment required"
        )

    try:
        assessed_top_up = Decimal(str(assessment["top_up"]))
    except (
        KeyError,
        TypeError,
        ValueError,
        ArithmeticError,
    ) as exc:
        raise SeveranceMinimumWageProvenanceError(
            "Invalid saved minimum wage top-up"
        ) from exc

    if (
        not assessed_top_up.is_finite()
        or assessed_top_up != minimum
    ):
        raise SeveranceMinimumWageProvenanceError(
            "Minimum wage assessment and posted lines differ"
        )

    if (
        proof.payroll_calculation_id != calculation.id
        or proof.revision != calculation.revision
        or not proof.historical_salary_verified
        or not proof.minimum_wage_rederived
    ):
        raise SeveranceMinimumWageProvenanceError(
            "Provenance and payroll revision mismatch"
        )

    return VerifiedEarningsCandidate(
        payroll_calculation_id=calculation.id,
        revision=calculation.revision,
        salary_amount=salary,
        minimum_wage_amount=minimum,
        gross_amount=gross,
        order100_inclusion_authorized=False,
        financial_execution_allowed=False,
    )
