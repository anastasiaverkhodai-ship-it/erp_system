from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.account import Account
from app.models.journal_entry import (
    JournalEntry,
    JournalEntryStatus,
)
from app.models.journal_entry_line import JournalEntryLine
from app.services.accounting_period_service import (
    ensure_period_open,
)


class AccountingReversalError(Exception):
    pass


class JournalEntryReversalNotFoundError(
    AccountingReversalError
):
    pass


def _resolve_reversal_tax_recognition_event_id(
    *,
    original_tax_recognition_event_id: int | None,
    override: int | None,
) -> int | None:
    """
    Preserve the original Tax Recognition typed source by default.

    OUTPUT VAT reversal accounting may override it with the
    immutable reversal TaxRecognitionEvent ID.
    """
    if override is None:
        return original_tax_recognition_event_id

    if override <= 0:
        raise AccountingReversalError(
            "Tax recognition event source override "
            "must be greater than zero"
        )

    if original_tax_recognition_event_id is None:
        raise AccountingReversalError(
            "Tax recognition event source override "
            "requires an original Tax Recognition source"
        )

    return override


def _resolve_reversal_sales_recognition_event_id(
    *,
    original_sales_recognition_event_id: int | None,
    override: int | None,
) -> int | None:
    """
    Preserve the original Sales typed source by default.

    Sales Recognition reversal accounting may override it
    with the immutable reversal SalesRecognitionEvent ID.
    """

    if override is None:
        return original_sales_recognition_event_id

    if override <= 0:
        raise AccountingReversalError(
            "Sales recognition event source override "
            "must be greater than zero"
        )

    if original_sales_recognition_event_id is None:
        raise AccountingReversalError(
            "Sales recognition event source override "
            "requires a Sales Recognition original "
            "journal entry"
        )

    return override


def _resolve_reversal_vat_advance_bridge_event_id(
    *,
    original_vat_advance_bridge_event_id: int | None,
    override: int | None,
) -> int | None:
    """
    Preserve the original VAT Advance Bridge typed source
    by default.

    VAT Advance Bridge reversal accounting may override it
    with the immutable reversal VatAdvanceBridgeEvent ID.
    """

    if override is None:
        return original_vat_advance_bridge_event_id

    if override <= 0:
        raise AccountingReversalError(
            "VAT advance bridge event source override "
            "must be greater than zero"
        )

    if original_vat_advance_bridge_event_id is None:
        raise AccountingReversalError(
            "VAT advance bridge event source override "
            "requires a VAT Advance Bridge original "
            "journal entry"
        )

    return override


def _resolve_reversal_input_vat_fulfillment_bridge_event_id(
    *,
    original_input_vat_fulfillment_bridge_event_id: int | None,
    override: int | None,
) -> int | None:
    """
    Preserve the original INPUT VAT Fulfillment Bridge typed source
    by default.

    INPUT VAT Fulfillment Bridge reversal accounting may override it
    with the immutable reversal InputVatFulfillmentBridgeEvent ID.
    """

    if override is None:
        return (
            original_input_vat_fulfillment_bridge_event_id
        )

    if override <= 0:
        raise AccountingReversalError(
            "INPUT VAT fulfillment bridge event "
            "source override must be greater than zero"
        )

    if (
        original_input_vat_fulfillment_bridge_event_id
        is None
    ):
        raise AccountingReversalError(
            "INPUT VAT fulfillment bridge event "
            "source override requires an original "
            "INPUT VAT Fulfillment Bridge journal entry"
        )

    return override


async def reverse_journal_entry(
    db: AsyncSession,
    company_id: int,
    journal_entry_id: int,
    reversal_date: date,
    reversed_by: int,
    tax_recognition_event_id_override: int | None = None,
    sales_recognition_event_id_override: int | None = None,
    vat_advance_bridge_event_id_override: int | None = None,
    input_vat_fulfillment_bridge_event_id_override: int | None = None,
) -> JournalEntry:
    result = await db.execute(
        select(JournalEntry)
        .options(
            selectinload(JournalEntry.lines)
        )
        .where(
            JournalEntry.id == journal_entry_id,
            JournalEntry.company_id == company_id,
        )
        .with_for_update()
    )

    original_entry = result.scalar_one_or_none()

    if original_entry is None:
        raise JournalEntryReversalNotFoundError(
            "Journal entry not found"
        )

    if (
        original_entry.status
        != JournalEntryStatus.POSTED
    ):
        raise AccountingReversalError(
            "Only posted journal entries can be reversed"
        )

    if original_entry.reversal_of_id is not None:
        raise AccountingReversalError(
            "A reversal journal entry cannot be reversed"
        )

    if not original_entry.lines:
        raise AccountingReversalError(
            "Journal entry has no lines to reverse"
        )

    await ensure_period_open(
        company_id=company_id,
        operation_date=reversal_date,
        db=db,
    )

    existing_reversal_result = await db.execute(
        select(JournalEntry.id).where(
            JournalEntry.reversal_of_id
            == original_entry.id
        )
    )

    if (
        existing_reversal_result.scalar_one_or_none()
        is not None
    ):
        raise AccountingReversalError(
            "Journal entry has already been reversed"
        )

    account_ids = {
        line.account_id
        for line in original_entry.lines
    }

    accounts_result = await db.execute(
        select(Account.id).where(
            Account.id.in_(account_ids),
            Account.company_id == company_id,
        )
    )

    valid_account_ids = set(
        accounts_result.scalars().all()
    )

    invalid_account_ids = (
        account_ids - valid_account_ids
    )

    if invalid_account_ids:
        raise AccountingReversalError(
            (
                "Journal entry contains accounts "
                "that do not belong to this company: "
                f"{sorted(invalid_account_ids)}"
            )
        )

    reversal_entry = JournalEntry(
        company_id=company_id,
        document_id=original_entry.document_id,
        payment_id=original_entry.payment_id,
        payment_settlement_allocation_id=(
            original_entry.payment_settlement_allocation_id
        ),
        tax_recognition_event_id=(
            _resolve_reversal_tax_recognition_event_id(
                original_tax_recognition_event_id=(
                    original_entry.tax_recognition_event_id
                ),
                override=(
                    tax_recognition_event_id_override
                ),
            )
        ),
        sales_recognition_event_id=(
            _resolve_reversal_sales_recognition_event_id(
                original_sales_recognition_event_id=(
                    original_entry.sales_recognition_event_id
                ),
                override=(
                    sales_recognition_event_id_override
                ),
            )
        ),
        vat_advance_bridge_event_id=(
            _resolve_reversal_vat_advance_bridge_event_id(
                original_vat_advance_bridge_event_id=(
                    original_entry.vat_advance_bridge_event_id
                ),
                override=(
                    vat_advance_bridge_event_id_override
                ),
            )
        ),
        input_vat_fulfillment_bridge_event_id=(
            _resolve_reversal_input_vat_fulfillment_bridge_event_id(
                original_input_vat_fulfillment_bridge_event_id=(
                    original_entry
                    .input_vat_fulfillment_bridge_event_id
                ),
                override=(
                    input_vat_fulfillment_bridge_event_id_override
                ),
            )
        ),
        accounting_rule_id=original_entry.accounting_rule_id,
        entry_date=reversal_date,
        description=(
            f"Reversal of JournalEntry "
            f"{original_entry.id}"
        ),
        status=JournalEntryStatus.POSTED,
        created_by=reversed_by,
        posted_at=datetime.utcnow(),
        reversal_of_id=original_entry.id,
    )

    reversal_entry.lines = [
        JournalEntryLine(
            line_no=line.line_no,
            account_id=line.account_id,
            debit=line.credit,
            credit=line.debit,
            description=(
                f"Reversal of line {line.line_no}"
            ),
        )
        for line in original_entry.lines
    ]

    db.add(reversal_entry)

    original_entry.status = (
        JournalEntryStatus.REVERSED
    )
    original_entry.reversed_at = datetime.utcnow()
    original_entry.reversed_by = reversed_by

    await db.flush()

    return reversal_entry
