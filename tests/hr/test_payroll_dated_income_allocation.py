from datetime import date
from decimal import Decimal

import pytest

from app.services.payroll_dated_income_allocation import (
    DatedIncome,
    TaxPolicyWindow,
    DatedIncomeAllocationError,
    allocate_verified_dated_income,
)


def day(number):
    return date(2026, 9, number)


def policies():
    return [
        TaxPolicyWindow(day(1), day(14), 101, 201, 301),
        TaxPolicyWindow(day(15), day(30), 102, 202, 302),
    ]


def income(source_id, start, end, amount):
    return DatedIncome(
        source_id,
        day(start),
        day(end),
        Decimal(amount),
    )


def test_two_verified_income_segments_keep_exact_amounts():
    allocations = allocate_verified_dated_income(
        income_lines=[
            income(11, 1, 14, "4000.00"),
            income(12, 15, 30, "6000.00"),
        ],
        policy_windows=policies(),
    )

    assert len(allocations) == 2
    assert [row.amount for row in allocations] == [
        Decimal("4000.00"),
        Decimal("6000.00"),
    ]
    assert [row.source_rate_id for row in allocations] == [
        101, 102,
    ]
    assert [row.source_profile_id for row in allocations] == [
        201, 202,
    ]
    assert [row.source_base_rule_id for row in allocations] == [
        301, 302,
    ]
    assert sum(
        (row.amount for row in allocations),
        Decimal("0.00"),
    ) == Decimal("10000.00")


def test_unsplit_income_crossing_rate_change_is_rejected():
    with pytest.raises(
        DatedIncomeAllocationError,
        match="crosses a tax policy boundary",
    ):
        allocate_verified_dated_income(
            income_lines=[
                income(11, 1, 30, "10000.00")
            ],
            policy_windows=policies(),
        )


def test_missing_policy_coverage_is_rejected():
    with pytest.raises(
        DatedIncomeAllocationError,
        match="complete dated coverage",
    ):
        allocate_verified_dated_income(
            income_lines=[
                income(11, 10, 20, "3000.00")
            ],
            policy_windows=[
                TaxPolicyWindow(day(1), day(12), 101, None, None),
            ],
        )


def test_duplicate_income_source_is_rejected():
    with pytest.raises(
        DatedIncomeAllocationError,
        match="duplicate income source line",
    ):
        allocate_verified_dated_income(
            income_lines=[
                income(11, 1, 5, "200.00"),
                income(11, 6, 10, "300.00"),
            ],
            policy_windows=policies(),
        )


def test_overlapping_policy_windows_are_rejected():
    with pytest.raises(
        DatedIncomeAllocationError,
        match="Overlapping",
    ):
        allocate_verified_dated_income(
            income_lines=[
                income(11, 1, 5, "100.00")
            ],
            policy_windows=[
                TaxPolicyWindow(day(1), day(20), 101, None, None),
                TaxPolicyWindow(day(15), day(30), 102, None, None),
            ],
        )


@pytest.mark.parametrize(
    "amount",
    ["-1.00", "NaN", "Infinity", "12.345"],
)
def test_invalid_amounts_are_rejected(amount):
    with pytest.raises(
        DatedIncomeAllocationError,
        match="Invalid verified income amount",
    ):
        allocate_verified_dated_income(
            income_lines=[
                income(11, 1, 14, amount)
            ],
            policy_windows=policies(),
        )


def test_empty_income_is_rejected():
    with pytest.raises(
        DatedIncomeAllocationError,
        match="Verified dated income lines",
    ):
        allocate_verified_dated_income(
            income_lines=[],
            policy_windows=policies(),
        )
