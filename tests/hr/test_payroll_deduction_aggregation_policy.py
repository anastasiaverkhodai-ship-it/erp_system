"""Phase 13 C2-B policy and aggregation contract tests."""

from decimal import Decimal as D

import pytest

from app.services.payroll_deduction_aggregation_policy import (
    LEGAL_CATEGORIES,
    DeductionPolicyError,
    DeductionPolicyLine,
    preview_deduction_policy,
)


def line(
    identifier,
    priority,
    requested,
    limit,
    category="employer_statutory",
):
    return DeductionPolicyLine(
        instruction_id=identifier,
        legal_category=category,
        priority=priority,
        requested_amount=D(requested),
        individual_limit=D(limit),
    )


def preview(
    lines,
    available="100.00",
    aggregate="60.00",
):
    return preview_deduction_policy(
        available_amount=D(available),
        aggregate_limit=D(aggregate),
        lines=tuple(lines),
    )


def test_empty_policy():
    result = preview(())

    assert result.result.total_allocated == D("0.00")
    assert result.result.allocations == ()


def test_priority_ordering():
    result = preview((
        line(3, 2, "50.00", "50.00"),
        line(1, 1, "50.00", "50.00"),
    ))

    allocations = result.result.allocations

    assert allocations[0].instruction_id == 1
    assert allocations[0].allocated_amount == D("50.00")

    assert allocations[1].instruction_id == 3
    assert allocations[1].allocated_amount == D("10.00")


def test_aggregate_limit():
    result = preview((
        line(1, 1, "100.00", "100.00"),
    ))

    assert result.result.total_allocated == D("60.00")


def test_available_amount_limit():
    result = preview(
        (line(1, 1, "100.00", "100.00"),),
        available="20.00",
    )

    assert result.result.total_allocated == D("20.00")


def test_individual_limit():
    result = preview((
        line(1, 1, "100.00", "25.00"),
    ))

    assert result.result.total_allocated == D("25.00")


def test_unallocated_remainder():
    result = preview((
        line(1, 1, "80.00", "30.00"),
    ))

    assert result.result.total_unallocated == D("50.00")


def test_equal_priority_blocked():
    with pytest.raises(
        DeductionPolicyError,
        match="Ambiguous",
    ):
        preview((
            line(1, 1, "10.00", "10.00"),
            line(2, 1, "20.00", "20.00"),
        ))


def test_duplicate_instruction_blocked():
    with pytest.raises(
        DeductionPolicyError,
        match="Duplicate",
    ):
        preview((
            line(1, 1, "10.00", "10.00"),
            line(1, 2, "20.00", "20.00"),
        ))


def test_unknown_category_blocked():
    with pytest.raises(
        DeductionPolicyError,
        match="Unknown legal category",
    ):
        preview((
            line(
                1, 1, "10.00", "10.00",
                category="unknown",
            ),
        ))


@pytest.mark.parametrize(
    "category",
    sorted(LEGAL_CATEGORIES),
)
def test_all_known_categories(category):
    result = preview((
        line(
            1,
            1,
            "10.00",
            "10.00",
            category=category,
        ),
    ))

    assert result.payroll_posting_allowed is False


@pytest.mark.parametrize(
    "amount",
    ["-1.00", "NaN", "Infinity", "1.001"],
)
def test_invalid_available_amount(amount):
    with pytest.raises(DeductionPolicyError):
        preview(
            (line(1, 1, "10.00", "10.00"),),
            available=amount,
        )


@pytest.mark.parametrize(
    "amount",
    ["-1.00", "NaN", "Infinity", "1.001"],
)
def test_invalid_aggregate_limit(amount):
    with pytest.raises(DeductionPolicyError):
        preview(
            (line(1, 1, "10.00", "10.00"),),
            aggregate=amount,
        )


@pytest.mark.parametrize(
    "amount",
    ["-1.00", "NaN", "Infinity", "1.001"],
)
def test_invalid_requested_amount(amount):
    with pytest.raises(DeductionPolicyError):
        preview((
            line(1, 1, amount, "10.00"),
        ))


@pytest.mark.parametrize(
    "amount",
    ["-1.00", "NaN", "Infinity", "1.001"],
)
def test_invalid_individual_limit(amount):
    with pytest.raises(DeductionPolicyError):
        preview((
            line(1, 1, "10.00", amount),
        ))


@pytest.mark.parametrize(
    "priority",
    [-1, True, 1.5],
)
def test_invalid_priority(priority):
    with pytest.raises(DeductionPolicyError):
        preview((
            line(1, priority, "10.00", "10.00"),
        ))


def test_zero_available_amount():
    result = preview(
        (line(1, 1, "100.00", "100.00"),),
        available="0.00",
    )

    assert result.result.total_allocated == D("0.00")
    assert result.result.total_unallocated == D("100.00")


def test_zero_aggregate_limit():
    result = preview(
        (line(1, 1, "100.00", "100.00"),),
        aggregate="0.00",
    )

    assert result.result.total_allocated == D("0.00")


def test_multiple_claims_conservation():
    result = preview((
        line(3, 3, "40.00", "40.00"),
        line(1, 1, "35.00", "30.00"),
        line(2, 2, "50.00", "50.00"),
    ))

    allocations = result.result.allocations

    assert result.result.total_allocated == D("60.00")

    for allocation in allocations:
        assert (
            allocation.allocated_amount
            + allocation.remaining_amount
            == allocation.requested_amount
        )

    assert (
        result.result.total_allocated
        + result.result.total_unallocated
        == D("125.00")
    )


def test_output_never_authorizes_payroll():
    result = preview((
        line(1, 1, "20.00", "20.00"),
    ))

    assert result.legal_authorization == "NOT_AUTHORIZED"
    assert result.payroll_posting_allowed is False


def test_no_database_write_methods():
    from pathlib import Path

    source = Path(
        "app/services/payroll_deduction_aggregation_policy.py"
    ).read_text()

    assert "db.commit(" not in source
    assert "db.flush(" not in source
    assert "UPDATE payroll" not in source
