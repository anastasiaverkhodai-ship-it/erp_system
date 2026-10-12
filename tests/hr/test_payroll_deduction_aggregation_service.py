"""Tests for the C2 non-posting aggregation planning engine."""

from decimal import Decimal as D

import pytest

from app.services.payroll_deduction_aggregation_service import (
    DeductionAggregationError,
    DeductionClaim,
    calculate_deduction_aggregation,
)


def claim(identifier, priority, requested, limit):
    return DeductionClaim(
        instruction_id=identifier,
        priority=priority,
        requested_amount=D(requested),
        individual_limit=D(limit),
    )


def calc(claims, available="100.00", aggregate="100.00"):
    return calculate_deduction_aggregation(
        available_amount=D(available),
        aggregate_limit=D(aggregate),
        claims=tuple(claims),
    )


def test_empty():
    result = calc(())
    assert result.total_allocated == D("0.00")
    assert result.allocations == ()


def test_priority_allocation():
    result = calc(
        (
            claim(2, 2, "60.00", "60.00"),
            claim(1, 1, "60.00", "60.00"),
        ),
        aggregate="80.00",
    )
    assert [x.instruction_id for x in result.allocations] == [1, 2]
    assert [x.allocated_amount for x in result.allocations] == [
        D("60.00"), D("20.00")
    ]


def test_individual_cap():
    result = calc(
        (claim(1, 1, "90.00", "25.00"),)
    )
    assert result.total_allocated == D("25.00")
    assert result.total_unallocated == D("65.00")


def test_aggregate_cap():
    result = calc(
        (claim(1, 1, "90.00", "90.00"),),
        aggregate="30.00",
    )
    assert result.total_allocated == D("30.00")


def test_available_cap():
    result = calc(
        (claim(1, 1, "90.00", "90.00"),),
        available="20.00",
    )
    assert result.total_allocated == D("20.00")


def test_equal_priority_determinism():
    result = calc(
        (
            claim(9, 1, "40.00", "40.00"),
            claim(2, 1, "40.00", "40.00"),
        ),
        aggregate="40.00",
    )
    assert result.allocations[0].instruction_id == 2


def test_duplicate_rejected():
    with pytest.raises(DeductionAggregationError):
        calc(
            (
                claim(1, 1, "10.00", "10.00"),
                claim(1, 2, "10.00", "10.00"),
            )
        )


@pytest.mark.parametrize(
    "amount",
    ["-1.00", "1.001", "NaN", "Infinity"],
)
def test_invalid_amount_rejected(amount):
    with pytest.raises(DeductionAggregationError):
        calc((), available=amount)


@pytest.mark.parametrize("priority", [-1, True])
def test_invalid_priority_rejected(priority):
    with pytest.raises(DeductionAggregationError):
        calc((claim(1, priority, "10.00", "10.00"),))


def test_zero_available():
    result = calc(
        (claim(1, 1, "40.00", "40.00"),),
        available="0.00",
    )
    assert result.total_allocated == D("0.00")
    assert result.total_unallocated == D("40.00")


def test_conservation():
    result = calc(
        (
            claim(3, 2, "40.00", "40.00"),
            claim(1, 1, "35.00", "30.00"),
            claim(2, 1, "50.00", "50.00"),
        ),
        aggregate="60.00",
    )

    assert result.total_allocated == D("60.00")

    for row in result.allocations:
        assert (
            row.allocated_amount + row.remaining_amount
            == row.requested_amount
        )

    assert (
        result.total_allocated
        + result.available_after_allocations
        == result.available_amount
    )


def test_no_database_writes():
    from pathlib import Path

    source = Path(
        "app/services/payroll_deduction_aggregation_service.py"
    ).read_text()

    assert "db.commit(" not in source
    assert "db.flush(" not in source
    assert "UPDATE payroll" not in source
