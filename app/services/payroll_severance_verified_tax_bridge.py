"""Fail-closed bridge from persisted payroll to severance-only finance.

A mixed-income payroll cannot attribute aggregate statutory taxes to
severance without independently verified per-income allocation.
No payment, journal or tax correction is authorized here.
"""
from decimal import Decimal
from sqlalchemy import select

from app.models.payroll import PayrollCalculation, PayrollCalculationLine
from app.services.payroll_severance_income_classification import (
    split_payroll_income,
)
from app.services.payroll_severance_tax_classification import (
    classify_severance_tax_bases,
)
from app.services.payroll_tax_balance_service import (
    PayrollStatutoryResult,
    PayrollStatutoryResultLine,
)
from app.services.payroll_severance_net_tax_settlement import (
    SeveranceFinancialSnapshot,
    SeveranceSettlementError,
)


class SeveranceTaxSourceError(ValueError):
    pass


def require_exclusive_severance(split):
    """Never allocate mixed-payroll totals to severance by assumption."""
    if (
        not isinstance(split.severance_gross, Decimal)
        or not isinstance(split.ordinary_gross, Decimal)
        or split.severance_gross <= 0
        or split.ordinary_gross != Decimal("0.00")
        or not split.severance_source_ids
    ):
        raise SeveranceTaxSourceError(
            "Mixed or missing severance income requires independent "
            "severance-only statutory allocation"
        )
    return split


async def load_severance_only_financial_snapshot(
    db, *, company_id, calculation_id, income_date, policies
):
    if (
        type(company_id) is not int or company_id <= 0
        or type(calculation_id) is not int or calculation_id <= 0
    ):
        raise SeveranceTaxSourceError("Invalid payroll source identity")

    calculation = await db.scalar(
        select(PayrollCalculation).where(
            PayrollCalculation.company_id == company_id,
            PayrollCalculation.id == calculation_id,
        )
    )
    if calculation is None:
        raise SeveranceTaxSourceError("Payroll calculation not found")

    lines = list((await db.scalars(
        select(PayrollCalculationLine).where(
            PayrollCalculationLine.company_id == company_id,
            PayrollCalculationLine.payroll_calculation_id == calculation_id,
        )
    )).all())

    split = split_payroll_income(
        gross_amount=calculation.gross_amount,
        currency_code=calculation.currency_code,
        lines=lines,
    )
    require_exclusive_severance(split)

    classification = classify_severance_tax_bases(
        split=split,
        income_date=income_date,
        policies=policies,
    )

    statutory = await db.scalar(
        select(PayrollStatutoryResult).where(
            PayrollStatutoryResult.company_id == company_id,
            PayrollStatutoryResult.payroll_calculation_id
            == calculation_id,
        )
    )
    if statutory is None or statutory.currency_code != "UAH":
        raise SeveranceTaxSourceError(
            "Verified UAH statutory result required"
        )

    statutory_lines = list((await db.scalars(
        select(PayrollStatutoryResultLine).where(
            PayrollStatutoryResultLine.company_id == company_id,
            PayrollStatutoryResultLine.payroll_statutory_result_id
            == statutory.id,
        )
    )).all())

    expected = {
        "personal_income_tax",
        "military_levy",
        "unified_social_contribution",
    }
    amounts = {}
    for line in statutory_lines:
        if (
            line.component not in expected
            or line.component in amounts
            or line.currency_code != "UAH"
        ):
            raise SeveranceTaxSourceError(
                "Invalid or duplicate statutory component attribution"
            )
        amounts[line.component] = Decimal(line.amount)

    if set(amounts) != expected:
        raise SeveranceTaxSourceError(
            "All three statutory components are required"
        )

    bases = (
        ("personal_income_tax_base", "personal_income_tax"),
        ("military_levy_base", "military_levy"),
        ("unified_social_contribution_base",
         "unified_social_contribution"),
    )

    for base_name, component in bases:
        if getattr(classification, base_name) == 0 and amounts[component] != 0:
            raise SeveranceTaxSourceError(
                f"Statutory amount contradicts dated policy: {component}"
            )

    gross = split.severance_gross
    pit = amounts["personal_income_tax"]
    levy = amounts["military_levy"]
    contribution = amounts["unified_social_contribution"]

    snapshot = SeveranceFinancialSnapshot(
        gross=gross,
        personal_income_tax=pit,
        military_levy=levy,
        net=gross - pit - levy,
        employer_contribution=contribution,
    )
    try:
        snapshot.validate()
    except SeveranceSettlementError as exc:
        raise SeveranceTaxSourceError(
            "Severance-only statutory values do not reconcile"
        ) from exc

    if snapshot.net != Decimal(statutory.net_amount):
        raise SeveranceTaxSourceError(
            "Statutory net differs from verified severance-only net"
        )

    return snapshot
