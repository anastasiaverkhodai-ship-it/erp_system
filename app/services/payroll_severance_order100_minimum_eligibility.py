"""Narrow Order 100 eligibility for verified 2026 wage top-ups.

Eligibility for average earnings is distinct from payment permission.
"""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal


class Order100MinimumEligibilityError(ValueError):
    pass


@dataclass(frozen=True)
class Order100MinimumEligibility:
    payroll_calculation_id: int
    revision: int
    minimum_wage_amount: Decimal
    eligible_for_average: bool
    financial_execution_allowed: bool
    rule_reference: str


RULE_VERSION = "UA-4695-IX-2026-v1"
RULE_SOURCE = "https://zakon.rada.gov.ua/laws/show/4695-IX"
ORDER100_SOURCE = "https://zakon.rada.gov.ua/laws/show/100-95-%D0%BF"


def _require(condition, message):
    if not condition:
        raise Order100MinimumEligibilityError(message)


def _value(item):
    return getattr(item, "value", item)


def authorize_verified_minimum_wage_for_average(
    *,
    candidate,
    calculation,
    period,
    lines,
):
    """Determine average-earnings eligibility from proven payroll data.

    The caller must first execute the database-backed historical salary
    and minimum-wage rederivation proof. This function only classifies
    the resulting payroll source and never authorizes payment.
    """
    _require(
        candidate.payroll_calculation_id == calculation.id
        and candidate.revision == calculation.revision
        and calculation.payroll_period_id == period.id,
        "Candidate does not match current calculation and period",
    )
    _require(
        candidate.financial_execution_allowed is False
        and candidate.order100_inclusion_authorized is False,
        "Unexpected candidate authorization state",
    )
    _require(
        _value(period.status) == "finalized"
        and _value(calculation.status) == "calculated"
        and calculation.currency_code == "UAH",
        "Finalized calculated UAH payroll required",
    )
    _require(
        date(2026, 1, 1)
        <= period.start_date
        <= period.end_date
        <= date(2026, 12, 31)
        and period.start_date.day == 1,
        "Dated Order 100 and minimum-wage rule review required",
    )
    assessment = calculation.minimum_wage_assessment
    _require(
        isinstance(assessment, dict)
        and assessment.get("status") == "assessed"
        and assessment.get("rule_version") == RULE_VERSION
        and assessment.get("source_reference") == RULE_SOURCE
        and isinstance(assessment.get("request"), dict),
        "Supported saved statutory assessment required",
    )
    amount = candidate.minimum_wage_amount
    _require(
        type(amount) is Decimal
        and amount.is_finite()
        and amount > 0
        and candidate.salary_amount > 0
        and candidate.salary_amount + amount
        == candidate.gross_amount
        == Decimal(calculation.gross_amount),
        "Proven wages do not reconcile",
    )
    try:
        saved = Decimal(str(assessment["top_up"]))
    except (ValueError, TypeError, ArithmeticError, KeyError) as exc:
        raise Order100MinimumEligibilityError(
            "Invalid saved top-up amount"
        ) from exc
    _require(
        saved.is_finite() and saved == amount,
        "Saved statutory top-up differs",
    )
    _require(
        lines
        and all(
            _value(line.line_type) in {"salary", "minimum_wage"}
            and line.currency_code == "UAH"
            and line.payroll_calculation_id == calculation.id
            for line in lines
        ),
        "Other earning categories require separate Order 100 proof",
    )
    topups = [
        line for line in lines
        if _value(line.line_type) == "minimum_wage"
    ]
    _require(
        len(topups) == 1
        and Decimal(topups[0].amount) == amount
        and topups[0].source_effective_from == period.start_date
        and topups[0].source_effective_to == period.end_date,
        "Top-up must belong to the evidenced payroll month",
    )

    return Order100MinimumEligibility(
        payroll_calculation_id=calculation.id,
        revision=calculation.revision,
        minimum_wage_amount=amount,
        eligible_for_average=True,
        financial_execution_allowed=False,
        rule_reference=ORDER100_SOURCE,
    )
