"""Cash chronology arithmetic and rejection contracts.

No live company money is created or modified.
"""

from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from app.services.cash_physical_chronology_service import (
    CashOpening,
    CashMovement,
    CashChronologyError,
    CashInsufficientFundsError,
    calculate_cash_chronology,
)


DAY = date(2026, 10, 1)


def opening(amount="100.00"):
    return CashOpening(
        company_id=1,
        cash_desk_id=3,
        currency_code="UAH",
        effective_date=DAY,
        amount=Decimal(amount),
        evidence_id=9,
    )


def movement(
    *,
    day=DAY,
    sequence=1,
    kind="payment_issue",
    source_id=1,
    direction="outgoing",
    amount="10.00",
):
    return CashMovement(
        company_id=1,
        cash_desk_id=3,
        currency_code="UAH",
        event_date=day,
        sequence=sequence,
        source_kind=kind,
        source_id=source_id,
        direction=direction,
        amount=Decimal(amount),
    )


def calculate(*items, base=None):
    return calculate_cash_chronology(
        opening=base or opening(),
        movements=tuple(items),
    )


def test_empty_cash_chronology():
    result = calculate()
    assert result.closing_balance == Decimal("100.00")
    assert result.points == ()


def test_zero_opening_rejects_withdrawal():
    with pytest.raises(CashInsufficientFundsError):
        calculate(
            movement(),
            base=opening("0.00"),
        )


def test_exact_balance_is_allowed():
    result = calculate(movement(amount="100.00"))
    assert result.closing_balance == Decimal("0.00")


def test_ordinary_and_payroll_share_cash():
    result = calculate(
        movement(
            sequence=1, direction="incoming",
            source_id=1, amount="50.00",
        ),
        movement(
            sequence=2, kind="payroll_issue",
            source_id=7, amount="120.00",
        ),
        movement(
            sequence=3, kind="payment_issue",
            source_id=8, amount="20.00",
        ),
    )
    assert result.closing_balance == Decimal("10.00")


def test_payroll_reversal_adds_cash_once():
    result = calculate(
        movement(
            sequence=1, kind="payroll_issue",
            source_id=7, amount="80.00",
        ),
        movement(
            sequence=2, kind="payroll_reversal",
            source_id=71, direction="incoming",
            amount="80.00",
        ),
    )
    assert result.closing_balance == Decimal("100.00")


def test_backdated_withdrawal_cannot_make_later_balance_negative():
    later = movement(
        day=date(2026, 10, 3),
        sequence=1,
        source_id=2,
        amount="90.00",
    )
    backdated = movement(
        day=date(2026, 10, 2),
        sequence=1,
        kind="payroll_issue",
        source_id=3,
        amount="20.00",
    )
    with pytest.raises(CashInsufficientFundsError):
        calculate(later, backdated)


def test_negative_at_any_intermediate_point_is_rejected():
    with pytest.raises(CashInsufficientFundsError):
        calculate(
            movement(sequence=1, amount="120.00"),
            movement(
                sequence=2,
                direction="incoming",
                source_id=2,
                amount="100.00",
            ),
        )


def test_duplicate_source_rejected():
    with pytest.raises(CashChronologyError):
        calculate(
            movement(sequence=1),
            movement(sequence=2),
        )


def test_same_day_sequence_collision_rejected():
    with pytest.raises(CashChronologyError):
        calculate(
            movement(sequence=1),
            movement(sequence=1, source_id=2),
        )


@pytest.mark.parametrize(
    "change",
    [
        {"company_id": 2},
        {"cash_desk_id": 4},
        {"currency_code": "EUR"},
    ],
)
def test_scope_isolation(change):
    with pytest.raises(CashChronologyError):
        calculate(replace(movement(), **change))


def test_event_before_opening_rejected():
    with pytest.raises(CashChronologyError):
        calculate(
            movement(day=date(2026, 9, 30))
        )


@pytest.mark.parametrize(
    "amount",
    ["-1.00", "NaN", "Infinity", "1.001", "0.00"],
)
def test_invalid_movement_money_rejected(amount):
    with pytest.raises(CashChronologyError):
        calculate(movement(amount=amount))


@pytest.mark.parametrize(
    "amount",
    ["-1.00", "NaN", "Infinity", "1.001"],
)
def test_invalid_opening_money_rejected(amount):
    with pytest.raises(CashChronologyError):
        calculate(base=opening(amount))


def test_unknown_source_rejected():
    with pytest.raises(CashChronologyError):
        calculate(movement(kind="cash_document"))


def test_event_order_is_deterministic():
    first = movement(
        sequence=1,
        source_id=1,
        direction="incoming",
        amount="40.00",
    )
    second = movement(
        sequence=2,
        source_id=2,
        amount="20.00",
    )
    a = calculate(first, second)
    b = calculate(second, first)
    assert a == b
    assert a.closing_balance == Decimal("120.00")


def test_opening_is_not_automatically_authorized():
    # Mathematical input is not an attestation or approval.
    result = calculate(base=opening("0.00"))
    assert result.closing_balance == Decimal("0.00")
    assert not hasattr(result, "is_approved")
