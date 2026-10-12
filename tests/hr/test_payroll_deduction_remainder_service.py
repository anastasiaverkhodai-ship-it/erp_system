"""Phase 13 C3-A: deduction remainder safety contracts."""

from decimal import Decimal

import pytest

from app.services.payroll_deduction_remainder_service import (
    DeductionRemainder,
    DeductionRemainderError,
    apply_deduction_remainder,
    open_deduction_remainder,
)


def opening(value="100.00"):
    return open_deduction_remainder(
        company_id=1,
        instruction_id=9,
        original_amount=Decimal(value),
    )


def apply(state, event, amount, *, company=1, instruction=9):
    return apply_deduction_remainder(
        state=state,
        company_id=company,
        instruction_id=instruction,
        event_id=event,
        allocated_amount=Decimal(amount),
    )


def test_opening_remainder():
    state = opening()

    assert state.original_amount == Decimal("100.00")
    assert state.applied_amount == Decimal("0.00")
    assert state.remaining_amount == Decimal("100.00")
    assert state.applied_event_ids == ()


def test_partial_allocation():
    result = apply(opening(), "run-1", "30.00")

    assert result.applied_now == Decimal("30.00")
    assert result.duplicate is False
    assert result.state.applied_amount == Decimal("30.00")
    assert result.state.remaining_amount == Decimal("70.00")


def test_sequential_allocations_and_carry_forward():
    first = apply(opening(), "run-1", "30.00")
    second = apply(first.state, "run-2", "20.00")

    assert second.state.applied_amount == Decimal("50.00")
    assert second.state.remaining_amount == Decimal("50.00")
    assert second.state.applied_event_ids == (
        "run-1",
        "run-2",
    )


def test_full_settlement():
    result = apply(opening(), "run-1", "100.00")

    assert result.state.remaining_amount == Decimal("0.00")


def test_duplicate_event_is_noop():
    first = apply(opening(), "run-1", "30.00")
    replay = apply(first.state, "run-1", "30.00")

    assert replay.duplicate is True
    assert replay.applied_now == Decimal("0.00")
    assert replay.state == first.state


def test_new_event_cannot_exceed_outstanding():
    first = apply(opening(), "run-1", "90.00")

    with pytest.raises(
        DeductionRemainderError,
        match="exceeds",
    ):
        apply(first.state, "run-2", "20.00")


def test_duplicate_event_does_not_change_state():
    first = apply(opening(), "run-1", "30.00")

    replay = apply(first.state, "run-1", "40.00")

    assert replay.state == first.state


def test_cross_company_blocked():
    with pytest.raises(
        DeductionRemainderError,
        match="another company",
    ):
        apply(opening(), "run-1", "10.00", company=2)


def test_wrong_instruction_blocked():
    with pytest.raises(
        DeductionRemainderError,
        match="instruction mismatch",
    ):
        apply(
            opening(),
            "run-1",
            "10.00",
            instruction=10,
        )


@pytest.mark.parametrize(
    "amount",
    ["-1.00", "0.00", "1.001", "NaN", "Infinity"],
)
def test_invalid_allocation_rejected(amount):
    with pytest.raises(DeductionRemainderError):
        apply(opening(), "run-1", amount)


@pytest.mark.parametrize(
    "amount",
    ["-1.00", "1.001", "NaN", "Infinity"],
)
def test_invalid_original_amount_rejected(amount):
    with pytest.raises(DeductionRemainderError):
        opening(amount)


@pytest.mark.parametrize(
    "event_id",
    ["", " ", " a", "a "],
)
def test_invalid_event_identifier_rejected(event_id):
    with pytest.raises(DeductionRemainderError):
        apply(opening(), event_id, "10.00")


def test_original_state_is_immutable():
    original = opening()

    result = apply(original, "run-1", "10.00")

    assert original.remaining_amount == Decimal("100.00")
    assert result.state.remaining_amount == Decimal("90.00")


def test_manual_invariant_violation_blocked():
    with pytest.raises(
        DeductionRemainderError,
        match="conservation",
    ):
        DeductionRemainder(
            company_id=1,
            instruction_id=9,
            original_amount=Decimal("100.00"),
            applied_amount=Decimal("30.00"),
            remaining_amount=Decimal("80.00"),
            applied_event_ids=("run-1",),
        )


def test_duplicate_event_ids_in_state_rejected():
    with pytest.raises(
        DeductionRemainderError,
        match="Duplicate",
    ):
        DeductionRemainder(
            company_id=1,
            instruction_id=9,
            original_amount=Decimal("100.00"),
            applied_amount=Decimal("30.00"),
            remaining_amount=Decimal("70.00"),
            applied_event_ids=("run-1", "run-1"),
        )


def test_no_database_or_posting_dependency():
    from pathlib import Path

    source = Path(
        "app/services/payroll_deduction_remainder_service.py"
    ).read_text(encoding="utf-8")

    assert "AsyncSession" not in source
    assert "await db.commit(" not in source
    assert "generate_and_post_payroll_deduction_journal(" not in source
