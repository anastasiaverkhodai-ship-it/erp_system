from dataclasses import dataclass
from decimal import Decimal

import pytest

from app.services.cash_payment_gl_evidence_service import (
    PaymentCashGLEvidenceError,
    validate_payment_cash_leg,
)


@dataclass
class Line:
    account_id: int
    debit: Decimal
    credit: Decimal


def leg(debit="0", credit="0", account_id=301):
    return Line(
        account_id,
        Decimal(debit),
        Decimal(credit),
    )


@pytest.mark.parametrize(
    "direction,reversal,debit,credit",
    [
        ("incoming", False, "100", "0"),
        ("outgoing", False, "0", "100"),
        ("incoming", True, "0", "100"),
        ("outgoing", True, "100", "0"),
    ],
)
def test_valid_cash_leg(
    direction, reversal, debit, credit
):
    validate_payment_cash_leg(
        lines=[leg(debit, credit)],
        cash_account_id=301,
        amount=Decimal("100"),
        direction=direction,
        reversal=reversal,
    )


@pytest.mark.parametrize(
    "lines",
    [
        [],
        [leg("100", "0", 302)],
        [leg("100", "0"), leg("100", "0")],
        [leg("99.99", "0")],
        [leg("0", "100")],
        [leg("100", "100")],
        [leg("NaN", "0")],
    ],
)
def test_reject_invalid_incoming_leg(lines):
    with pytest.raises(PaymentCashGLEvidenceError):
        validate_payment_cash_leg(
            lines=lines,
            cash_account_id=301,
            amount=Decimal("100"),
            direction="incoming",
        )


@pytest.mark.parametrize(
    "amount",
    [
        Decimal("0"),
        Decimal("-1"),
        Decimal("NaN"),
        Decimal("Infinity"),
        Decimal("1.001"),
    ],
)
def test_reject_bad_amount(amount):
    with pytest.raises(PaymentCashGLEvidenceError):
        validate_payment_cash_leg(
            lines=[leg("100", "0")],
            cash_account_id=301,
            amount=amount,
            direction="incoming",
        )


@pytest.mark.parametrize(
    "account,direction",
    [
        (None, "incoming"),
        (0, "incoming"),
        (True, "incoming"),
        (301, "invalid"),
    ],
)
def test_reject_bad_identity_or_direction(account, direction):
    with pytest.raises(PaymentCashGLEvidenceError):
        validate_payment_cash_leg(
            lines=[leg("100", "0")],
            cash_account_id=account,
            amount=Decimal("100"),
            direction=direction,
        )
