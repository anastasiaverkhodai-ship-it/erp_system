"""Read authoritative ordinary and payroll cash journal events.

Cash documents are immutable evidence, NOT extra cash movements.

This is a read-only adapter. It does not authorize disbursement,
serialize writers, or commit. Its caller must lock the cash desk
and validate the chronology before an atomic financial posting.
"""

from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.journal_entry import JournalEntry
from app.models.payment import Payment
from app.models.payroll_disbursement import PayrollDisbursement
from app.services.cash_physical_chronology_service import (
    CashMovement,
)


class CashEventSourceError(ValueError):
    pass


def _status(value):
    return str(getattr(value, "value", value)).lower()


def _money(value):
    amount = Decimal(value)
    if (
        not amount.is_finite()
        or amount <= 0
        or amount.as_tuple().exponent < -2
    ):
        raise CashEventSourceError(
            "Invalid physical cash source amount"
        )
    return amount


async def read_physical_cash_events(
    db: AsyncSession,
    *,
    company_id: int,
    cash_desk_id: int,
    currency_code: str,
    effective_date: date,
) -> tuple[CashMovement, ...]:
    """Read cash sources and their posted reversal journals.

    Journal IDs provide stable same-day ordering. The caller must
    hold the desk lock when enforcing a balance under concurrency.
    """

    if (
        type(company_id) is not int
        or type(cash_desk_id) is not int
        or company_id <= 0
        or cash_desk_id <= 0
        or type(effective_date) is not date
        or not isinstance(currency_code, str)
        or len(currency_code) != 3
        or not currency_code.isalpha()
        or currency_code != currency_code.upper()
    ):
        raise CashEventSourceError(
            "Invalid cash event reader scope"
        )

    payment_rows = (
        await db.execute(
            select(JournalEntry, Payment)
            .join(
                Payment,
                (Payment.id == JournalEntry.payment_id)
                & (Payment.company_id == JournalEntry.company_id),
            )
            .where(
                JournalEntry.company_id == company_id,
                JournalEntry.reversal_of_id.is_(None),
                Payment.cash_desk_id == cash_desk_id,
            )
        )
    ).all()

    payroll_rows = (
        await db.execute(
            select(JournalEntry, PayrollDisbursement)
            .join(
                PayrollDisbursement,
                (
                    PayrollDisbursement.id
                    == JournalEntry.payroll_disbursement_id
                )
                & (
                    PayrollDisbursement.company_id
                    == JournalEntry.company_id
                ),
            )
            .where(
                JournalEntry.company_id == company_id,
                JournalEntry.reversal_of_id.is_(None),
                PayrollDisbursement.cash_desk_id == cash_desk_id,
            )
        )
    ).all()

    original_ids = [
        journal.id
        for journal, _ in payment_rows + payroll_rows
    ]

    if len(original_ids) != len(set(original_ids)):
        raise CashEventSourceError(
            "Duplicate original cash journal source"
        )

    reversals = {}

    if original_ids:
        rows = (
            await db.scalars(
                select(JournalEntry).where(
                    JournalEntry.company_id == company_id,
                    JournalEntry.reversal_of_id.in_(original_ids),
                )
            )
        ).all()

        for reversal in rows:
            if reversal.reversal_of_id in reversals:
                raise CashEventSourceError(
                    "Multiple reversals for one cash journal"
                )
            reversals[reversal.reversal_of_id] = reversal

    events = []

    def append_event(journal, source, kind, direction):
        if source.currency_code != currency_code:
            raise CashEventSourceError(
                "Cash movement currency mismatch"
            )

        status = _status(journal.status)
        if status == "draft":
            if journal.id in reversals:
                raise CashEventSourceError(
                    "Draft journal has a reversal"
                )
            return

        if status not in ("posted", "reversed"):
            raise CashEventSourceError(
                "Unrecognised original cash journal status"
            )

        amount = _money(source.amount)

        if journal.entry_date < effective_date:
            raise CashEventSourceError(
                "Historical cash movement predates "
                "the independently reviewed opening"
            )

        events.append(
            CashMovement(
                company_id=company_id,
                cash_desk_id=cash_desk_id,
                currency_code=currency_code,
                event_date=journal.entry_date,
                sequence=journal.id,
                source_kind=kind + "_issue",
                source_id=journal.id,
                direction=direction,
                amount=amount,
            )
        )

        reversal = reversals.get(journal.id)

        if reversal is None:
            if status == "reversed":
                raise CashEventSourceError(
                    "Reversed cash journal has no reversal event"
                )
            return

        if status != "reversed" or _status(reversal.status) != "posted":
            raise CashEventSourceError(
                "Cash journal reversal status mismatch"
            )

        if reversal.entry_date < journal.entry_date:
            raise CashEventSourceError(
                "Cash reversal predates original journal"
            )

        reverse_direction = (
            "incoming"
            if direction == "outgoing"
            else "outgoing"
        )

        events.append(
            CashMovement(
                company_id=company_id,
                cash_desk_id=cash_desk_id,
                currency_code=currency_code,
                event_date=reversal.entry_date,
                sequence=reversal.id,
                source_kind=kind + "_reversal",
                source_id=reversal.id,
                direction=reverse_direction,
                amount=amount,
            )
        )

    for journal, payment in payment_rows:
        from app.services.cash_payment_gl_evidence_service import (
            PaymentCashGLEvidenceError,
            verify_payment_cash_gl,
        )

        direction = _status(payment.direction)
        if direction not in ("incoming", "outgoing"):
            raise CashEventSourceError(
                "Unknown ordinary payment direction"
            )

        try:
            verified_original, verified_reversal = (
                await verify_payment_cash_gl(
                    db,
                    company_id=company_id,
                    payment_id=payment.id,
                )
            )
        except PaymentCashGLEvidenceError as exc:
            raise CashEventSourceError(
                "Ordinary Payment cash GL evidence invalid"
            ) from exc

        observed_reversal = reversals.get(journal.id)
        observed_reversal_id = (
            observed_reversal.id
            if observed_reversal is not None
            else None
        )

        if (
            verified_original != journal.id
            or verified_reversal != observed_reversal_id
        ):
            raise CashEventSourceError(
                "Ordinary cash journal identity mismatch"
            )

        append_event(journal, payment, "payment", direction)

    for journal, payout in payroll_rows:
        from app.services.cash_payroll_gl_evidence_service import (
            verify_payroll_cash_gl,
            PayrollCashGLEvidenceError,
        )

        try:
            verified_original_id, verified_reversal_id = (
                await verify_payroll_cash_gl(
                    db,
                    company_id=company_id,
                    payroll_disbursement_id=payout.id,
                )
            )
        except PayrollCashGLEvidenceError as exc:
            raise CashEventSourceError(
                "Payroll cash journal failed GL evidence "
                "verification"
            ) from exc

        if verified_original_id != journal.id:
            raise CashEventSourceError(
                "Payroll original journal identity mismatch"
            )

        observed_reversal = reversals.get(journal.id)
        observed_reversal_id = (
            observed_reversal.id
            if observed_reversal is not None
            else None
        )

        if verified_reversal_id != observed_reversal_id:
            raise CashEventSourceError(
                "Payroll reversal journal identity mismatch"
            )

        append_event(journal, payout, "payroll", "outgoing")

    # One JournalEntry may represent only one financial source.
    journal_ids = [event.source_id for event in events]
    if len(journal_ids) != len(set(journal_ids)):
        raise CashEventSourceError(
            "Cash event journal identity collision"
        )

    return tuple(
        sorted(
            events,
            key=lambda event: (
                event.event_date,
                event.sequence,
            ),
        )
    )
