"""Direct aggregation tests for dated monthly employee taxes."""

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.services.payroll_dated_monthly_tax_aggregation import (
    aggregate_dated_monthly_employee_taxes as aggregate,
    DatedMonthlyTaxAggregationError,
)


def row(
    component,
    amount,
    rate_id,
    start,
    end,
    **kwargs,
):
    data = dict(
        component=component,
        amount=Decimal(amount),
        currency_code="UAH",
        source_rate_id=rate_id,
        rate_effective_from=start,
        rate_effective_to=end,
        source_tax_profile_id=None,
        source_base_rule_id=None,
    )
    data.update(kwargs)
    return SimpleNamespace(**data)


A = date(2026, 9, 1)
B = date(2026, 9, 14)
C = date(2026, 9, 15)
D = date(2026, 9, 30)

PIT = "personal_income_tax"
LEVY = "military_levy"
USC = "unified_social_contribution"


def standard():
    return [
        row(PIT, "180.00", 1, A, D),
        row(LEVY, "50.00", 2, A, D),
        row(USC, "220.00", 3, A, D),
    ]


def two_rate():
    return [
        row(PIT, "108.00", 1, A, B),
        row(PIT, "76.00", 4, C, D),
        row(LEVY, "50.00", 2, A, D),
        row(USC, "220.00", 3, A, D),
    ]


def test_standard_legacy():
    amounts = aggregate(standard())
    assert amounts[PIT] == Decimal("180.00")
    assert amounts[LEVY] == Decimal("50.00")
    assert USC not in amounts


def test_two_rate_total():
    amounts = aggregate(two_rate())
    assert amounts[PIT] == Decimal("184.00")
    assert amounts[LEVY] == Decimal("50.00")


def test_order_independence():
    rows = two_rate()
    assert aggregate(rows) == aggregate(list(reversed(rows)))


@pytest.mark.parametrize("component", [PIT, LEVY])
def test_missing_component_rejected(component):
    rows = [
        line for line in standard()
        if line.component != component
    ]
    with pytest.raises(
        DatedMonthlyTaxAggregationError,
        match="Both monthly",
    ):
        aggregate(rows)


def test_duplicate_rate_id_rejected():
    rows = two_rate()
    rows[1].source_rate_id = 1
    with pytest.raises(
        DatedMonthlyTaxAggregationError,
        match="provenance",
    ):
        aggregate(rows)


def test_overlap_rejected():
    rows = two_rate()
    rows[1].rate_effective_from = B
    with pytest.raises(
        DatedMonthlyTaxAggregationError,
        match="Overlapping",
    ):
        aggregate(rows)


def test_gap_rejected():
    rows = two_rate()
    rows[1].rate_effective_from = date(2026, 9, 16)
    with pytest.raises(
        DatedMonthlyTaxAggregationError,
        match="discontinuous",
    ):
        aggregate(rows)


def test_unknown_component_rejected():
    rows = standard()
    rows.append(row("unknown_tax", "10.00", 9, A, D))
    with pytest.raises(
        DatedMonthlyTaxAggregationError,
        match="Unsupported",
    ):
        aggregate(rows)


@pytest.mark.parametrize("amount", [
    "-1.00", "1.001", "NaN",
])
def test_invalid_amount_rejected(amount):
    rows = standard()
    rows[0].amount = Decimal(amount)
    with pytest.raises(
        DatedMonthlyTaxAggregationError,
        match="Invalid",
    ):
        aggregate(rows)


def test_wrong_currency_rejected():
    rows = standard()
    rows[0].currency_code = "EUR"
    with pytest.raises(
        DatedMonthlyTaxAggregationError,
        match="currency",
    ):
        aggregate(rows)


def test_special_policy_rejected():
    rows = two_rate()
    rows[1].source_base_rule_id = 10
    with pytest.raises(
        DatedMonthlyTaxAggregationError,
        match="employee-wide",
    ):
        aggregate(rows)


def test_open_ended_multi_rate_rejected():
    rows = two_rate()
    rows[1].rate_effective_to = None
    with pytest.raises(
        DatedMonthlyTaxAggregationError,
        match="provenance",
    ):
        aggregate(rows)


def test_same_rate_two_lines_rejected():
    rows = standard()
    rows.insert(1, row(PIT, "10.00", 1, C, D))
    with pytest.raises(
        DatedMonthlyTaxAggregationError,
        match="provenance",
    ):
        aggregate(rows)


def test_input_unmodified():
    rows = two_rate()
    before = [(r.component, r.amount) for r in rows]
    aggregate(rows)
    assert before == [(r.component, r.amount) for r in rows]
