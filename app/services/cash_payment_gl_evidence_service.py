"""Strict historical GL verification for ordinary cash Payment.

No journal mutation, commit, or physical balance authorization.
Legacy payments without verifiable snapshots fail closed.
"""

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.journal_entry import JournalEntry
from app.models.journal_entry_line import JournalEntryLine
from app.models.payment import Payment


class PaymentCashGLEvidenceError(ValueError):
    pass


def _status(value):
    return str(getattr(value, "value", value)).lower()


def validate_payment_cash_leg(
    *,
    lines,
    cash_account_id,
    amount,
    direction,
    reversal=False,
):
    if type(cash_account_id) is not int or cash_account_id <= 0:
        raise PaymentCashGLEvidenceError(
            "Historical cash account snapshot is required"
        )
    if type(reversal) is not bool:
        raise PaymentCashGLEvidenceError("Invalid reversal flag")
    if direction not in ("incoming", "outgoing"):
        raise PaymentCashGLEvidenceError(
            "Unrecognized cash direction"
        )

    try:
        value = Decimal(amount)
    except (TypeError, ValueError, ArithmeticError) as exc:
        raise PaymentCashGLEvidenceError(
            "Invalid cash amount"
        ) from exc

    if (
        not value.is_finite()
        or value <= 0
        or value.as_tuple().exponent < -2
    ):
        raise PaymentCashGLEvidenceError(
            "Cash amount must be positive, finite and exact"
        )

    cash_legs = [
        line for line in lines
        if line.account_id == cash_account_id
    ]

    if len(cash_legs) != 1:
        raise PaymentCashGLEvidenceError(
            "Expected exactly one historical cash GL leg"
        )

    leg = cash_legs[0]

    try:
        debit = Decimal(leg.debit)
        credit = Decimal(leg.credit)
    except (TypeError, ValueError, ArithmeticError) as exc:
        raise PaymentCashGLEvidenceError(
            "Invalid cash GL leg"
        ) from exc

    if not debit.is_finite() or not credit.is_finite():
        raise PaymentCashGLEvidenceError(
            "Nonfinite GL amount"
        )

    is_debit = (direction == "incoming") != reversal

    expected = (
        (value, Decimal("0"))
        if is_debit
        else (Decimal("0"), value)
    )

    if (debit, credit) != expected:
        raise PaymentCashGLEvidenceError(
            "Cash GL direction/amount differs from Payment"
        )


async def verify_payment_cash_gl(
    db: AsyncSession,
    *,
    company_id: int,
    payment_id: int,
) -> tuple[int, int | None]:
    if (
        type(company_id) is not int
        or type(payment_id) is not int
        or company_id <= 0
        or payment_id <= 0
    ):
        raise PaymentCashGLEvidenceError(
            "Invalid payment/company identity"
        )

    payment = await db.scalar(
        select(Payment).where(
            Payment.company_id == company_id,
            Payment.id == payment_id,
        )
    )

    if payment is None:
        raise PaymentCashGLEvidenceError(
            "Payment not found for company"
        )

    if (
        payment.cash_desk_id is None
        or payment.bank_account_id is not None
        or payment.cash_account_id_snapshot is None
    ):
        raise PaymentCashGLEvidenceError(
            "Historical ordinary cash Payment required"
        )

    direction = _status(payment.direction)
    if direction not in ("incoming", "outgoing"):
        raise PaymentCashGLEvidenceError(
            "Payment direction is invalid"
        )

    originals = (
        await db.scalars(
            select(JournalEntry).where(
                JournalEntry.company_id == company_id,
                JournalEntry.payment_id == payment_id,
                JournalEntry.reversal_of_id.is_(None),
            )
        )
    ).all()

    if len(originals) != 1:
        raise PaymentCashGLEvidenceError(
            "Expected one original Payment journal"
        )

    original = originals[0]

    if _status(original.status) not in ("posted", "reversed"):
        raise PaymentCashGLEvidenceError(
            "Original Payment journal not posted"
        )

    if original.entry_date != payment.payment_date:
        raise PaymentCashGLEvidenceError(
            "Payment original journal date mismatch"
        )

    original_lines = (
        await db.scalars(
            select(JournalEntryLine).where(
                JournalEntryLine.journal_entry_id == original.id
            )
        )
    ).all()

    validate_payment_cash_leg(
        lines=original_lines,
        cash_account_id=payment.cash_account_id_snapshot,
        amount=payment.amount,
        direction=direction,
    )

    reversals = (
        await db.scalars(
            select(JournalEntry).where(
                JournalEntry.company_id == company_id,
                JournalEntry.reversal_of_id == original.id,
            )
        )
    ).all()

    if len(reversals) > 1:
        raise PaymentCashGLEvidenceError(
            "Multiple Payment reversal journals"
        )

    if not reversals:
        if _status(original.status) != "posted":
            raise PaymentCashGLEvidenceError(
                "Reversed Payment missing reversal journal"
            )
        return original.id, None

    reversal = reversals[0]

    if (
        _status(original.status) != "reversed"
        or _status(reversal.status) != "posted"
        or reversal.entry_date < original.entry_date
    ):
        raise PaymentCashGLEvidenceError(
            "Payment reversal chronology/status mismatch"
        )

    reversal_lines = (
        await db.scalars(
            select(JournalEntryLine).where(
                JournalEntryLine.journal_entry_id == reversal.id
            )
        )
    ).all()

    validate_payment_cash_leg(
        lines=reversal_lines,
        cash_account_id=payment.cash_account_id_snapshot,
        amount=payment.amount,
        direction=direction,
        reversal=True,
    )

    return original.id, reversal.id
