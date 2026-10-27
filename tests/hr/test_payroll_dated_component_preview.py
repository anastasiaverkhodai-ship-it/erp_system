from datetime import date
from decimal import Decimal

import pytest

from app.services.payroll_dated_income_allocation import DatedIncomeAllocation
from app.services.payroll_dated_component_preview import (
    DatedComponentPreviewError,
    preview_dated_component,
)


def item(source, rate_id, amount, profile=None, rule=None):
    return DatedIncomeAllocation(
        source_line_id=source,
        effective_from=date(2026, 9, 1),
        effective_to=date(2026, 9, 14),
        amount=Decimal(amount),
        source_rate_id=rate_id,
        source_profile_id=profile,
        source_base_rule_id=rule,
    )


def test_two_rates_keep_distinct_exact_bases():
    rows = preview_dated_component(
        allocations=(
            item(1, 101, "4000.00"),
            item(2, 102, "6000.00"),
        ),
        verified_rates={
            101: Decimal("0.18"),
            102: Decimal("0.10"),
        },
        expected_gross=Decimal("10000.00"),
    )
    assert [x.amount for x in rows] == [
        Decimal("720.00"), Decimal("600.00")
    ]
    assert [x.source_rate_id for x in rows] == [101, 102]


def test_group_rounding_is_deterministic():
    rows = preview_dated_component(
        allocations=(
            item(1, 101, "0.01"),
            item(2, 101, "0.01"),
        ),
        verified_rates={101: Decimal("0.25")},
        expected_gross=Decimal("0.02"),
    )
    assert len(rows) == 1
    assert rows[0].amount == Decimal("0.01")


def test_zero_base_is_supported():
    rows = preview_dated_component(
        allocations=(item(1, 101, "0.00"),),
        verified_rates={101: Decimal("0.18")},
        expected_gross=Decimal("0.00"),
    )
    assert rows[0].amount == Decimal("0.00")


@pytest.mark.parametrize("rate", [
    Decimal("-0.01"), Decimal("1.01"),
    Decimal("NaN"), Decimal("Infinity"),
])
def test_invalid_rate_rejected(rate):
    with pytest.raises(DatedComponentPreviewError):
        preview_dated_component(
            allocations=(item(1, 101, "100.00"),),
            verified_rates={101: rate},
            expected_gross=Decimal("100.00"),
        )


def test_missing_rate_rejected():
    with pytest.raises(DatedComponentPreviewError):
        preview_dated_component(
            allocations=(item(1, 101, "100.00"),),
            verified_rates={},
            expected_gross=Decimal("100.00"),
        )


def test_duplicate_source_rejected():
    with pytest.raises(DatedComponentPreviewError):
        preview_dated_component(
            allocations=(
                item(1, 101, "40.00"),
                item(1, 102, "60.00"),
            ),
            verified_rates={
                101: Decimal("0.18"),
                102: Decimal("0.10"),
            },
            expected_gross=Decimal("100.00"),
        )


def test_wrong_gross_rejected():
    with pytest.raises(DatedComponentPreviewError):
        preview_dated_component(
            allocations=(item(1, 101, "100.00"),),
            verified_rates={101: Decimal("0.18")},
            expected_gross=Decimal("200.00"),
        )


@pytest.mark.parametrize("profile,rule", [(1, None), (None, 2)])
def test_special_policy_not_silently_calculated(profile, rule):
    with pytest.raises(DatedComponentPreviewError):
        preview_dated_component(
            allocations=(item(1, 101, "100.00", profile, rule),),
            verified_rates={101: Decimal("0.18")},
            expected_gross=Decimal("100.00"),
        )
