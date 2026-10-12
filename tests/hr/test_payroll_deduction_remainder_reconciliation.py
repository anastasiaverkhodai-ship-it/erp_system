"""C3-C1 source reconciliation contracts."""

from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from app.services.payroll_deduction_remainder_ledger import (
    DeductionLedger,
    DeductionLedgerEvent,
    DeductionLedgerError,
    open_deduction_ledger,
    append_deduction_ledger_event,
)

from app.services.payroll_deduction_remainder_reconciliation import (
    DeductionSourceSnapshot,
    DeductionSourceReconciliationError,
    reconcile_deduction_source,
)


def make_source(
    *,
    company_id=1,
    instruction_id=10,
    original_amount="100.00",
    currency_code="UAH",
):
    return DeductionSourceSnapshot(
        company_id=company_id,
        instruction_id=instruction_id,
        original_amount=Decimal(original_amount),
        currency_code=currency_code,
    )


def make_ledger(amounts=("30.00", "20.00")):
    ledger = open_deduction_ledger(
        company_id=1,
        instruction_id=10,
        original_amount=Decimal("100.00"),
    )

    for index, amount in enumerate(amounts):
        application = append_deduction_ledger_event(
            ledger=ledger,
            event=DeductionLedgerEvent(
                event_id=f"source-event-{index}",
                company_id=1,
                instruction_id=10,
                amount=Decimal(amount),
            ),
        )

        ledger = application.ledger

    return ledger


def check(*, source=None, ledger=None, currency="UAH"):
    return reconcile_deduction_source(
        source=source if source is not None else make_source(),
        ledger=ledger if ledger is not None else make_ledger(),
        stored_currency_code=currency,
    )


def test_source_reconciliation_success():
    result = check()

    assert result.reconciled is True
    assert result.original_amount == Decimal("100.00")
    assert result.applied_amount == Decimal("50.00")
    assert result.remaining_amount == Decimal("50.00")
    assert result.event_count == 2


def test_no_events():
    result = check(ledger=make_ledger(()))

    assert result.event_count == 0
    assert result.applied_amount == Decimal("0.00")
    assert result.remaining_amount == Decimal("100.00")


def test_full_settlement():
    result = check(
        ledger=make_ledger(("100.00",)),
    )

    assert result.applied_amount == Decimal("100.00")
    assert result.remaining_amount == Decimal("0.00")


def test_company_isolation():
    with pytest.raises(
        DeductionSourceReconciliationError,
        match="Company isolation",
    ):
        check(source=make_source(company_id=2))


def test_instruction_isolation():
    with pytest.raises(
        DeductionSourceReconciliationError,
        match="Instruction identity",
    ):
        check(source=make_source(instruction_id=11))


def test_source_original_amount_mismatch():
    with pytest.raises(
        DeductionSourceReconciliationError,
        match="Original amount",
    ):
        check(
            source=make_source(original_amount="90.00")
        )


def test_currency_mismatch():
    with pytest.raises(
        DeductionSourceReconciliationError,
        match="Currency mismatch",
    ):
        check(currency="EUR")


@pytest.mark.parametrize(
    "value",
    ["-1.00", "1.001", "NaN", "Infinity"],
)
def test_invalid_original_amount(value):
    with pytest.raises(
        DeductionSourceReconciliationError,
    ):
        make_source(original_amount=value)


@pytest.mark.parametrize(
    "currency",
    ["uah", "UA", "UAHH", "U1H", ""],
)
def test_invalid_source_currency(currency):
    with pytest.raises(
        DeductionSourceReconciliationError,
    ):
        make_source(currency_code=currency)


@pytest.mark.parametrize(
    "company, instruction",
    [
        (0, 10),
        (-1, 10),
        (1, 0),
        (1, -1),
        (True, 10),
    ],
)
def test_invalid_identifiers(company, instruction):
    with pytest.raises(
        DeductionSourceReconciliationError,
    ):
        make_source(
            company_id=company,
            instruction_id=instruction,
        )


def test_source_snapshot_is_immutable():
    snapshot = make_source()

    with pytest.raises(FrozenInstanceError):
        snapshot.original_amount = Decimal("200.00")


def test_result_is_immutable():
    result = check()

    with pytest.raises(FrozenInstanceError):
        result.remaining_amount = Decimal("0.00")


def test_posting_remains_disabled():
    result = check()

    assert result.payroll_posting_allowed is False
    assert result.legal_authorization == "NOT_AUTHORIZED"


def test_invalid_source_object():
    with pytest.raises(
        DeductionSourceReconciliationError,
        match="Invalid source",
    ):
        check(source=object())


def test_invalid_ledger_object():
    with pytest.raises(
        DeductionSourceReconciliationError,
        match="Invalid ledger",
    ):
        check(ledger=object())


def test_duplicate_events_rejected():
    # DeductionLedger is validated on construction.
    # Invalid histories must never become valid ledger objects.
    event = DeductionLedgerEvent(
        event_id="duplicate-id",
        company_id=1,
        instruction_id=10,
        amount=Decimal("10.00"),
    )

    with pytest.raises(
        DeductionLedgerError,
        match="Duplicate event in ledger history",
    ):
        DeductionLedger(
            company_id=1,
            instruction_id=10,
            original_amount=Decimal("100.00"),
            events=(event, event),
        )


def test_no_database_dependencies():
    from pathlib import Path

    source = Path(
        "app/services/payroll_deduction_remainder_reconciliation.py"
    ).read_text(encoding="utf-8")

    assert "AsyncSession" not in source
    assert "db.commit(" not in source
    assert "db.rollback(" not in source
    assert "pg_advisory_xact_lock" not in source
