"""Strict payroll cash GL evidence validation.

This is a validation-only component:
- no Payment creation
- no journal posting
- no financial mutation
- no cash spending authorization

Checks the exact debit/credit leg on the historical cash
account snapshot, including the posted reversal.

The caller must separately prove desk cash availability.
"""

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.journal_entry import JournalEntry
from app.models.journal_entry_line import JournalEntryLine
from app.models.payroll_disbursement import PayrollDisbursement


class PayrollCashGLEvidenceError(ValueError):
    pass


def _status(value):
    return str(getattr(value, "value", value)).lower()


def _amount(value):
    if not isinstance(value, Decimal):
        value = Decimal(value)
    if (
        not value.is_finite()
        or value <= 0
        or value.as_tuple().exponent < -2
    ):
        raise PayrollCashGLEvidenceError(
            "Invalid payroll cash amount"
        )
    return value


def validate_cash_money_leg(
    *,
    lines,
    cash_account_id: int,
    amount: Decimal,
    reversed_direction: bool = False,
):
    """Require one exact cash-account leg, without ambiguity."""

    if type(cash_account_id) is not int or cash_account_id <= 0:
        raise PayrollCashGLEvidenceError(
            "Historical cash account ID is invalid"
        )

    value = _amount(amount)
    selected = [
        item for item in lines
        if item.account_id == cash_account_id
    ]

    if len(selected) != 1:
        raise PayrollCashGLEvidenceError(
            "Expected exactly one historical cash GL leg"
        )

    leg = selected[0]
    debit = Decimal(leg.debit)
    credit = Decimal(leg.credit)

    if not debit.is_finite() or not credit.is_finite():
        raise PayrollCashGLEvidenceError(
            "Nonfinite payroll GL leg"
        )

    if reversed_direction:
        expected = (value, Decimal("0"))
    else:
        expected = (Decimal("0"), value)

    if (debit, credit) != expected:
        raise PayrollCashGLEvidenceError(
            "Payroll cash GL amount or direction mismatch"
        )


async def verify_payroll_cash_gl(
    db: AsyncSession,
    *,
    company_id: int,
    payroll_disbursement_id: int,
) -> tuple[int, int | None]:
    """Verify original cash leg and optional posted reversal.

    Returns original journal ID and optional reversal journal ID.
    Does not certify an actual physical cash balance.
    """

    if (
        type(company_id) is not int
        or type(payroll_disbursement_id) is not int
        or min(company_id, payroll_disbursement_id) <= 0
    ):
        raise PayrollCashGLEvidenceError(
            "Invalid company or payroll identity"
        )

    payout = await db.scalar(
        select(PayrollDisbursement).where(
            PayrollDisbursement.company_id == company_id,
            PayrollDisbursement.id == payroll_disbursement_id,
        )
    )

    if payout is None:
        raise PayrollCashGLEvidenceError(
            "Company payroll payout missing"
        )

    if (
        payout.cash_desk_id is None
        or payout.bank_account_id is not None
        or payout.cash_account_id_snapshot is None
    ):
        raise PayrollCashGLEvidenceError(
            "Cash payout with historical account required"
        )

    original_rows = (
        await db.scalars(
            select(JournalEntry).where(
                JournalEntry.company_id == company_id,
                JournalEntry.payroll_disbursement_id
                == payroll_disbursement_id,
                JournalEntry.reversal_of_id.is_(None),
            )
        )
    ).all()

    if len(original_rows) != 1:
        raise PayrollCashGLEvidenceError(
            "Expected exactly one original payroll journal"
        )

    original = original_rows[0]

    if _status(original.status) not in ("posted", "reversed"):
        raise PayrollCashGLEvidenceError(
            "Original payroll journal is not posted"
        )

    amount = _amount(payout.amount)

    original_lines = (
        await db.scalars(
            select(JournalEntryLine).where(
                JournalEntryLine.journal_entry_id == original.id,
            )
        )
    ).all()

    validate_cash_money_leg(
        lines=original_lines,
        cash_account_id=payout.cash_account_id_snapshot,
        amount=amount,
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
        raise PayrollCashGLEvidenceError(
            "Multiple reversal journals"
        )

    if not reversals:
        if _status(original.status) != "posted":
            raise PayrollCashGLEvidenceError(
                "Reversed payroll has no reversal"
            )
        return original.id, None

    reversal = reversals[0]

    if (
        _status(original.status) != "reversed"
        or _status(reversal.status) != "posted"
        or reversal.entry_date < original.entry_date
    ):
        raise PayrollCashGLEvidenceError(
            "Payroll reversal GL state is inconsistent"
        )

    reversal_lines = (
        await db.scalars(
            select(JournalEntryLine).where(
                JournalEntryLine.journal_entry_id == reversal.id,
            )
        )
    ).all()

    validate_cash_money_leg(
        lines=reversal_lines,
        cash_account_id=payout.cash_account_id_snapshot,
        amount=amount,
        reversed_direction=True,
    )

    return original.id, reversal.id
