from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.services.payroll_severance_order100_classifier import (
    Order100ClassificationError,
    classify_order100_line,
    require_supported_severance_lines,
)


def line(kind, amount="100.00", currency="UAH"):
    return SimpleNamespace(
        line_type=kind,
        amount=Decimal(amount),
        currency_code=currency,
    )


@pytest.mark.parametrize("kind", [
    "minimum_wage",
    "supplement",
    "manual_adjustment",
])
def test_requires_provenance(kind):
    decision = classify_order100_line(
        line_type=kind,
        amount=Decimal("10"),
        currency_code="UAH",
    )
    assert decision.treatment == "requires_proof"

    with pytest.raises(
        Order100ClassificationError,
        match="Non-salary earnings require",
    ):
        require_supported_severance_lines(
            [line("salary"), line(kind, "10")],
            gross_amount=Decimal("110"),
        )


@pytest.mark.parametrize("kind", [
    "vacation_pay",
    "sick_pay",
    "leave_compensation",
    "severance",
])
def test_two_month_excluded_types_not_silently_counted(kind):
    decision = classify_order100_line(
        line_type=kind,
        amount=Decimal("10"),
        currency_code="UAH",
    )
    assert decision.treatment == "exclude"

    with pytest.raises(
        Order100ClassificationError,
        match="Non-salary earnings require",
    ):
        require_supported_severance_lines(
            [line("salary"), line(kind, "10")],
            gross_amount=Decimal("110"),
        )


def test_salary_only_reconciles():
    assert require_supported_severance_lines(
        [line("salary", "9000"), line("salary", "3000")],
        gross_amount=Decimal("12000"),
    ) == Decimal("12000")


def test_gross_mismatch_rejected():
    with pytest.raises(
        Order100ClassificationError,
        match="reconcile",
    ):
        require_supported_severance_lines(
            [line("salary", "100")],
            gross_amount=Decimal("200"),
        )


@pytest.mark.parametrize("kind", [
    "unknown",
    "",
    None,
])
def test_unknown_categories_blocked(kind):
    with pytest.raises(Order100ClassificationError):
        classify_order100_line(
            line_type=kind,
            amount=Decimal("10"),
            currency_code="UAH",
        )


@pytest.mark.parametrize("amount", [
    Decimal("NaN"),
    Decimal("Infinity"),
    Decimal("-Infinity"),
])
def test_nonfinite_values_blocked(amount):
    with pytest.raises(Order100ClassificationError):
        classify_order100_line(
            line_type="salary",
            amount=amount,
            currency_code="UAH",
        )


def test_wrong_currency_blocked():
    with pytest.raises(
        Order100ClassificationError,
        match="UAH",
    ):
        classify_order100_line(
            line_type="salary",
            amount=Decimal("100"),
            currency_code="EUR",
        )


def test_negative_salary_blocked():
    with pytest.raises(Order100ClassificationError):
        classify_order100_line(
            line_type="salary",
            amount=Decimal("-100"),
            currency_code="UAH",
        )


def test_empty_month_blocked():
    with pytest.raises(Order100ClassificationError):
        require_supported_severance_lines(
            [],
            gross_amount=Decimal("0"),
        )


def test_all_declared_types_are_classified():
    kinds = (
        "salary",
        "minimum_wage",
        "supplement",
        "manual_adjustment",
        "vacation_pay",
        "sick_pay",
        "leave_compensation",
        "severance",
    )
    treatments = {
        kind: classify_order100_line(
            line_type=kind,
            amount=Decimal("1"),
            currency_code="UAH",
        ).treatment
        for kind in kinds
    }
    assert treatments["salary"] == "include"
    assert len(treatments) == 8


def test_verified_minimum_wage_explicit_contract():
    from decimal import Decimal
    from types import SimpleNamespace
    from app.services.payroll_severance_order100_classifier import (
        require_supported_severance_lines,
    )

    salary = SimpleNamespace(
        line_type="salary",
        amount=Decimal("4000.00"),
        currency_code="UAH",
    )
    minimum = SimpleNamespace(
        line_type="minimum_wage",
        amount=Decimal("1200.00"),
        currency_code="UAH",
    )

    assert require_supported_severance_lines(
        [salary, minimum],
        gross_amount=Decimal("5200.00"),
        verified_minimum_wage_amount=Decimal("1200.00"),
    ) == Decimal("5200.00")


@pytest.mark.parametrize("verified", [
    None,
    Decimal("1199.99"),
    Decimal("1200.01"),
    Decimal("0"),
    Decimal("-1"),
    Decimal("NaN"),
    "1200.00",
])
def test_minimum_wage_without_exact_proof_is_blocked(verified):
    from decimal import Decimal
    from types import SimpleNamespace
    from app.services.payroll_severance_order100_classifier import (
        Order100ClassificationError,
        require_supported_severance_lines,
    )

    lines = [
        SimpleNamespace(
            line_type="salary",
            amount=Decimal("4000.00"),
            currency_code="UAH",
        ),
        SimpleNamespace(
            line_type="minimum_wage",
            amount=Decimal("1200.00"),
            currency_code="UAH",
        ),
    ]

    with pytest.raises(Order100ClassificationError):
        require_supported_severance_lines(
            lines,
            gross_amount=Decimal("5200.00"),
            verified_minimum_wage_amount=verified,
        )


def test_other_components_remain_blocked_with_minimum_wage_proof():
    from decimal import Decimal
    from types import SimpleNamespace
    from app.services.payroll_severance_order100_classifier import (
        Order100ClassificationError,
        require_supported_severance_lines,
    )

    lines = [
        SimpleNamespace(
            line_type="salary",
            amount=Decimal("4000.00"),
            currency_code="UAH",
        ),
        SimpleNamespace(
            line_type="minimum_wage",
            amount=Decimal("1200.00"),
            currency_code="UAH",
        ),
        SimpleNamespace(
            line_type="manual_adjustment",
            amount=Decimal("100.00"),
            currency_code="UAH",
        ),
    ]

    with pytest.raises(Order100ClassificationError):
        require_supported_severance_lines(
            lines,
            gross_amount=Decimal("5300.00"),
            verified_minimum_wage_amount=Decimal("1200.00"),
        )
