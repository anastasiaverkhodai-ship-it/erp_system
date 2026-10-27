from dataclasses import dataclass
from decimal import Decimal

import pytest

from app.services.cash_payroll_gl_evidence_service import (
    PayrollCashGLEvidenceError,
    validate_cash_money_leg,
)


@dataclass
class Line:
    account_id: int
    debit: Decimal
    credit: Decimal


def line(account=301, debit="0.00", credit="50.00"):
    return Line(
        account_id=account,
        debit=Decimal(debit),
        credit=Decimal(credit),
    )


def test_original_cash_credit():
    validate_cash_money_leg(
        lines=[line()],
        cash_account_id=301,
        amount=Decimal("50.00"),
    )


def test_reversal_cash_debit():
    validate_cash_money_leg(
        lines=[line(debit="50.00", credit="0.00")],
        cash_account_id=301,
        amount=Decimal("50.00"),
        reversed_direction=True,
    )


@pytest.mark.parametrize(
    "lines",
    [
        [],
        [line(account=302)],
        [line(), line()],
        [line(credit="49.99")],
        [line(debit="50.00", credit="0.00")],
        [line(debit="50.00", credit="50.00")],
        [line(credit="NaN")],
    ],
)
def test_invalid_original_cash_leg(lines):
    with pytest.raises(PayrollCashGLEvidenceError):
        validate_cash_money_leg(
            lines=lines,
            cash_account_id=301,
            amount=Decimal("50.00"),
        )


@pytest.mark.parametrize(
    "lines",
    [
        [],
        [line()],
        [line(debit="49.99", credit="0.00")],
        [line(debit="50.00", credit="1.00")],
        [line(debit="50.00", credit="0.00"),
         line(debit="50.00", credit="0.00")],
    ],
)
def test_invalid_reversal_cash_leg(lines):
    with pytest.raises(PayrollCashGLEvidenceError):
        validate_cash_money_leg(
            lines=lines,
            cash_account_id=301,
            amount=Decimal("50.00"),
            reversed_direction=True,
        )


@pytest.mark.parametrize(
    "bad",
    [
        Decimal("0"),
        Decimal("-1"),
        Decimal("NaN"),
        Decimal("Infinity"),
        Decimal("1.001"),
    ],
)
def test_bad_cash_amount(bad):
    with pytest.raises(PayrollCashGLEvidenceError):
        validate_cash_money_leg(
            lines=[line()],
            cash_account_id=301,
            amount=bad,
        )


def test_historical_account_identity():
    validate_cash_money_leg(
        lines=[line(account=401), line(account=301)],
        cash_account_id=301,
        amount=Decimal("50.00"),
    )
    with pytest.raises(PayrollCashGLEvidenceError):
        validate_cash_money_leg(
            lines=[line(account=401)],
            cash_account_id=301,
            amount=Decimal("50.00"),
        )
