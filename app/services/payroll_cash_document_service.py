"""Payroll cash document evidence.

This service never creates a generic Payment or GL journal.
The caller owns the database transaction.
"""

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.journal_entry import JournalEntry
from app.models.payroll_disbursement import (
    PayrollCashDocument,
    PayrollDisbursement,
)


class PayrollCashDocumentError(ValueError):
    pass


def _status_value(value):
    return getattr(value, "value", value)


def _document_number(company_id: int, payout_id: int) -> str:
    return f"PAYROLL-CASH-{company_id}-{payout_id}"


async def ensure_payroll_cash_issue_document(
    db: AsyncSession,
    *,
    company_id: int,
    payroll_disbursement_id: int,
    created_by: int,
) -> PayrollCashDocument:
    """Create or reuse immutable evidence for posted cash payroll.

    Locks the original payroll payout to serialize retries.
    Never creates a second Payment or journal.
    """

    if (
        company_id <= 0
        or payroll_disbursement_id <= 0
        or created_by <= 0
    ):
        raise PayrollCashDocumentError(
            "Company, payout and actor IDs must be positive"
        )

    payout = await db.scalar(
        select(PayrollDisbursement)
        .where(
            PayrollDisbursement.company_id == company_id,
            PayrollDisbursement.id == payroll_disbursement_id,
        )
        .with_for_update()
    )

    if payout is None:
        raise PayrollCashDocumentError(
            "Payroll disbursement not found in company"
        )

    if payout.bank_account_id is not None:
        raise PayrollCashDocumentError(
            "Bank payroll cannot produce a cash issue document"
        )

    if (
        payout.cash_desk_id is None
        or payout.cash_account_id_snapshot is None
    ):
        raise PayrollCashDocumentError(
            "Verified cash source and account snapshot required"
        )

    existing = await db.scalar(
        select(PayrollCashDocument).where(
            PayrollCashDocument.company_id == company_id,
            PayrollCashDocument.payroll_disbursement_id
            == payroll_disbursement_id,
            PayrollCashDocument.event_kind == "issue",
        )
    )

    expected_number = _document_number(
        company_id, payroll_disbursement_id
    )

    if existing is not None:
        if (
            existing.cash_desk_id != payout.cash_desk_id
            or existing.document_date != payout.payment_date
            or Decimal(existing.amount) != Decimal(payout.amount)
            or existing.currency_code != payout.currency_code
            or existing.document_number != expected_number
        ):
            raise PayrollCashDocumentError(
                "Existing cash document conflicts with payroll facts"
            )
        return existing

    journal = await db.scalar(
        select(JournalEntry).where(
            JournalEntry.company_id == company_id,
            JournalEntry.payroll_disbursement_id
            == payroll_disbursement_id,
            JournalEntry.reversal_of_id.is_(None),
        )
    )

    if journal is None or _status_value(journal.status) != "posted":
        raise PayrollCashDocumentError(
            "Original posted payroll journal is required"
        )

    if payout.cancelled_at is not None:
        raise PayrollCashDocumentError(
            "Cancelled payroll payout cannot create an issue"
        )

    amount = Decimal(payout.amount)

    if (
        not amount.is_finite()
        or amount <= Decimal("0")
    ):
        raise PayrollCashDocumentError(
            "Cash payout amount must be finite and positive"
        )

    document = PayrollCashDocument(
        company_id=company_id,
        cash_desk_id=payout.cash_desk_id,
        payroll_disbursement_id=payout.id,
        document_number=expected_number,
        event_kind="issue",
        document_date=payout.payment_date,
        amount=amount,
        currency_code=payout.currency_code,
        created_by=created_by,
        reversal_of_id=None,
    )

    db.add(document)
    await db.flush()

    return document


async def ensure_payroll_cash_reversal_document(
    db: AsyncSession,
    *,
    company_id: int,
    payroll_disbursement_id: int,
    created_by: int,
) -> PayrollCashDocument:
    """Append immutable cash reversal evidence after posted GL reversal.

    Does not create Payment, reverse a journal, or commit a transaction.
    """

    if (
        company_id <= 0
        or payroll_disbursement_id <= 0
        or created_by <= 0
    ):
        raise PayrollCashDocumentError(
            "Company, payout and actor IDs must be positive"
        )

    payout = await db.scalar(
        select(PayrollDisbursement)
        .where(
            PayrollDisbursement.company_id == company_id,
            PayrollDisbursement.id == payroll_disbursement_id,
        )
        .with_for_update()
    )

    if payout is None:
        raise PayrollCashDocumentError(
            "Payroll payout not found in company"
        )

    if (
        payout.bank_account_id is not None
        or payout.cash_desk_id is None
        or payout.cash_account_id_snapshot is None
    ):
        raise PayrollCashDocumentError(
            "Verified cash payroll payout required"
        )

    original_document = await db.scalar(
        select(PayrollCashDocument).where(
            PayrollCashDocument.company_id == company_id,
            PayrollCashDocument.payroll_disbursement_id
            == payout.id,
            PayrollCashDocument.event_kind == "issue",
        )
    )

    if original_document is None:
        raise PayrollCashDocumentError(
            "Original cash issue document is required"
        )

    if (
        original_document.cash_desk_id != payout.cash_desk_id
        or Decimal(original_document.amount) != Decimal(payout.amount)
        or original_document.currency_code != payout.currency_code
    ):
        raise PayrollCashDocumentError(
            "Original cash document and payout differ"
        )

    original_journal = await db.scalar(
        select(JournalEntry).where(
            JournalEntry.company_id == company_id,
            JournalEntry.payroll_disbursement_id == payout.id,
            JournalEntry.reversal_of_id.is_(None),
        )
    )

    if original_journal is None:
        raise PayrollCashDocumentError(
            "Original payroll journal missing"
        )

    reversal_journal = await db.scalar(
        select(JournalEntry).where(
            JournalEntry.company_id == company_id,
            JournalEntry.reversal_of_id == original_journal.id,
        )
    )

    if (
        reversal_journal is None
        or _status_value(reversal_journal.status) != "posted"
    ):
        raise PayrollCashDocumentError(
            "Posted payroll reversal journal is required"
        )

    expected_number = (
        f"PAYROLL-CASH-REV-{company_id}-{payout.id}"
    )

    existing = await db.scalar(
        select(PayrollCashDocument).where(
            PayrollCashDocument.company_id == company_id,
            PayrollCashDocument.reversal_of_id
            == original_document.id,
        )
    )

    if existing is not None:
        if (
            existing.event_kind != "reversal"
            or existing.payroll_disbursement_id != payout.id
            or existing.cash_desk_id != payout.cash_desk_id
            or existing.document_date != reversal_journal.entry_date
            or Decimal(existing.amount) != Decimal(payout.amount)
            or existing.document_number != expected_number
        ):
            raise PayrollCashDocumentError(
                "Historical cash reversal evidence conflicts"
            )
        return existing

    document = PayrollCashDocument(
        company_id=company_id,
        cash_desk_id=payout.cash_desk_id,
        payroll_disbursement_id=payout.id,
        document_number=expected_number,
        event_kind="reversal",
        document_date=reversal_journal.entry_date,
        amount=Decimal(payout.amount),
        currency_code=payout.currency_code,
        created_by=created_by,
        reversal_of_id=original_document.id,
    )

    db.add(document)
    await db.flush()
    return document
