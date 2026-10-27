from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.services.payroll_severance_order100_minimum_eligibility import (
    Order100MinimumEligibilityError,
    authorize_verified_minimum_wage_for_average,
)


def evidence():
    period = SimpleNamespace(
        id=20, status="finalized",
        start_date=date(2026, 8, 1),
        end_date=date(2026, 8, 31),
    )
    calculation = SimpleNamespace(
        id=10, revision=2, payroll_period_id=20,
        status="calculated", currency_code="UAH",
        gross_amount=Decimal("5000"),
        minimum_wage_assessment={
            "status": "assessed",
            "rule_version": "UA-4695-IX-2026-v1",
            "source_reference":
                "https://zakon.rada.gov.ua/laws/show/4695-IX",
            "request": {"norm_reference": "Calendar corrected"},
            "top_up": "1000",
        },
    )
    candidate = SimpleNamespace(
        payroll_calculation_id=10,
        revision=2,
        salary_amount=Decimal("4000"),
        minimum_wage_amount=Decimal("1000"),
        gross_amount=Decimal("5000"),
        order100_inclusion_authorized=False,
        financial_execution_allowed=False,
    )
    lines = [
        SimpleNamespace(
            payroll_calculation_id=10,
            line_type="salary", currency_code="UAH",
            amount=Decimal("4000"),
            source_effective_from=date(2026, 8, 1),
            source_effective_to=date(2026, 8, 31),
        ),
        SimpleNamespace(
            payroll_calculation_id=10,
            line_type="minimum_wage", currency_code="UAH",
            amount=Decimal("1000"),
            source_effective_from=date(2026, 8, 1),
            source_effective_to=date(2026, 8, 31),
        ),
    ]
    return dict(
        candidate=candidate,
        calculation=calculation,
        period=period,
        lines=lines,
    )


def test_eligible_top_up_remains_payment_blocked():
    data = evidence()
    result = authorize_verified_minimum_wage_for_average(**data)
    assert result.eligible_for_average is True
    assert result.financial_execution_allowed is False
    assert data["candidate"].order100_inclusion_authorized is False


@pytest.mark.parametrize("mutation", [
    "wrong_revision",
    "draft",
    "wrong_rule",
    "altered_assessment",
    "altered_line",
    "wrong_period",
    "other_earnings",
    "already_authorized",
])
def test_invalid_minimum_wage_proof_is_blocked(mutation):
    data = evidence()
    c = data["candidate"]
    calc = data["calculation"]
    period = data["period"]
    top = data["lines"][1]

    if mutation == "wrong_revision":
        c.revision = 1
    elif mutation == "draft":
        period.status = "draft"
    elif mutation == "wrong_rule":
        calc.minimum_wage_assessment["rule_version"] = "unknown"
    elif mutation == "altered_assessment":
        calc.minimum_wage_assessment["top_up"] = "1001"
    elif mutation == "altered_line":
        top.amount = Decimal("999")
    elif mutation == "wrong_period":
        top.source_effective_to = date(2026, 9, 1)
    elif mutation == "other_earnings":
        top.line_type = "manual_adjustment"
    elif mutation == "already_authorized":
        c.order100_inclusion_authorized = True

    with pytest.raises(Order100MinimumEligibilityError):
        authorize_verified_minimum_wage_for_average(**data)
