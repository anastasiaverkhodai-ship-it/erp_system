"""Phase 13 C3-B1: ledger and reconciliation contracts."""

from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from app.services.payroll_deduction_remainder_ledger import (
    DeductionLedger,
    DeductionLedgerEvent,
    DeductionLedgerError,
    append_deduction_ledger_event,
    open_deduction_ledger,
    reconcile_deduction_ledger,
)


def ledger():
    return open_deduction_ledger(
        company_id=1,
        instruction_id=9,
        original_amount=Decimal("100.00"),
    )


def event(key, amount, company=1, instruction=9):
    return DeductionLedgerEvent(
        event_id=key,
        company_id=company,
        instruction_id=instruction,
        amount=Decimal(amount),
    )


def append(state, entry):
    return append_deduction_ledger_event(
        ledger=state,
        event=entry,
    )


def test_opening_balance():
    balance = reconcile_deduction_ledger(ledger())

    assert balance.original_amount == Decimal("100.00")
    assert balance.applied_amount == Decimal("0.00")
    assert balance.remaining_amount == Decimal("100.00")


def test_partial_allocation():
    result = append(ledger(), event("run-1", "30.00"))

    assert result.applied_now == Decimal("30.00")
    assert result.duplicate is False
    assert result.remainder.remaining_amount == Decimal("70.00")


def test_carry_forward_multiple_periods():
    first = append(ledger(), event("oct", "30.00"))
    second = append(first.ledger, event("nov", "20.00"))
    third = append(second.ledger, event("dec", "50.00"))

    assert third.remainder.applied_amount == Decimal("100.00")
    assert third.remainder.remaining_amount == Decimal("0.00")
    assert len(third.ledger.events) == 3


def test_identical_event_replay_is_noop():
    first = append(ledger(), event("run-1", "30.00"))
    replay = append(
        first.ledger,
        event("run-1", "30.00"),
    )

    assert replay.duplicate is True
    assert replay.applied_now == Decimal("0.00")
    assert replay.ledger == first.ledger


def test_changed_amount_replay_blocked():
    first = append(ledger(), event("run-1", "30.00"))

    with pytest.raises(
        DeductionLedgerError,
        match="different payload",
    ):
        append(
            first.ledger,
            event("run-1", "40.00"),
        )


def test_over_allocation_blocked():
    first = append(ledger(), event("one", "90.00"))

    with pytest.raises(
        DeductionLedgerError,
        match="exceeds",
    ):
        append(
            first.ledger,
            event("two", "20.00"),
        )


def test_cross_company_blocked():
    with pytest.raises(
        DeductionLedgerError,
        match="Cross-company",
    ):
        append(
            ledger(),
            event("run-1", "10.00", company=2),
        )


def test_wrong_instruction_blocked():
    with pytest.raises(
        DeductionLedgerError,
        match="Instruction mismatch",
    ):
        append(
            ledger(),
            event("run-1", "10.00", instruction=10),
        )


@pytest.mark.parametrize(
    "amount",
    ["0.00", "-1.00", "1.001", "NaN", "Infinity"],
)
def test_invalid_event_amount_blocked(amount):
    with pytest.raises(DeductionLedgerError):
        event("bad", amount)


def test_replay_from_event_history():
    first = append(ledger(), event("a", "10.00"))
    second = append(first.ledger, event("b", "15.00"))

    reconstructed = reconcile_deduction_ledger(
        second.ledger
    )

    assert reconstructed.applied_amount == Decimal("25.00")
    assert reconstructed.remaining_amount == Decimal("75.00")
    assert reconstructed.applied_event_ids == ("a", "b")


def test_manual_duplicate_history_rejected():
    entry = event("same", "10.00")

    with pytest.raises(
        DeductionLedgerError,
        match="Duplicate event",
    ):
        DeductionLedger(
            company_id=1,
            instruction_id=9,
            original_amount=Decimal("100.00"),
            events=(entry, entry),
        )


def test_corrupt_overallocated_history_rejected():
    with pytest.raises(
        DeductionLedgerError,
        match="exceeds",
    ):
        DeductionLedger(
            company_id=1,
            instruction_id=9,
            original_amount=Decimal("100.00"),
            events=(
                event("a", "80.00"),
                event("b", "30.00"),
            ),
        )


def test_original_ledger_is_immutable():
    original = ledger()
    result = append(
        original,
        event("run-1", "10.00"),
    )

    assert original.events == ()
    assert len(result.ledger.events) == 1

    with pytest.raises(FrozenInstanceError):
        original.company_id = 2


def test_replay_after_full_settlement():
    first = append(ledger(), event("final", "100.00"))

    replay = append(
        first.ledger,
        event("final", "100.00"),
    )

    assert replay.duplicate is True
    assert replay.remainder.remaining_amount == Decimal("0.00")

    with pytest.raises(DeductionLedgerError):
        append(
            first.ledger,
            event("another", "1.00"),
        )


def test_no_database_write_or_posting_dependency():
    from pathlib import Path

    source = Path(
        "app/services/payroll_deduction_remainder_ledger.py"
    ).read_text(encoding="utf-8")

    assert "AsyncSession" not in source
    assert "db.commit(" not in source
    assert "generate_and_post_payroll_deduction_journal(" not in source
