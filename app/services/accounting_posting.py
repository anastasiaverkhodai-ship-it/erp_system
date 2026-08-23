from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.account import Account
from app.models.journal_entry import (
    JournalEntry,
    JournalEntryStatus,
)
from app.services.accounting_period_service import (
    ensure_period_open,
)


class AccountingPostingError(Exception):
    pass


class JournalEntryNotFoundError(AccountingPostingError):
    pass


async def validate_journal_entry(
    db: AsyncSession,
    journal_entry: JournalEntry,
) -> None:
    if not journal_entry.lines:
        raise AccountingPostingError(
            "Journal entry must contain lines"
        )

    if len(journal_entry.lines) < 2:
        raise AccountingPostingError(
            "Journal entry must contain at least two lines"
        )

    total_debit = sum(
        (
            line.debit or Decimal("0")
            for line in journal_entry.lines
        ),
        Decimal("0"),
    )

    total_credit = sum(
        (
            line.credit or Decimal("0")
            for line in journal_entry.lines
        ),
        Decimal("0"),
    )

    if total_debit <= 0:
        raise AccountingPostingError(
            "Journal entry total must be greater than zero"
        )

    if total_debit != total_credit:
        raise AccountingPostingError(
            (
                "Journal entry is not balanced: "
                f"debit={total_debit}, "
                f"credit={total_credit}"
            )
        )

    account_ids = {
        line.account_id
        for line in journal_entry.lines
    }

    result = await db.execute(
        select(Account.id).where(
            Account.id.in_(account_ids),
            Account.company_id
            == journal_entry.company_id,
            Account.is_active.is_(True),
            Account.is_postable.is_(True),
        )
    )

    valid_account_ids = set(
        result.scalars().all()
    )

    invalid_account_ids = (
        account_ids - valid_account_ids
    )

    if invalid_account_ids:
        raise AccountingPostingError(
            (
                "Journal entry contains invalid, inactive, "
                "non-postable, or foreign-company accounts: "
                f"{sorted(invalid_account_ids)}"
            )
        )


async def post_journal_entry(
    db: AsyncSession,
    company_id: int,
    journal_entry_id: int,
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

    journal_entry = result.scalar_one_or_none()

    if journal_entry is None:
        raise JournalEntryNotFoundError(
            "Journal entry not found"
        )

    if (
        journal_entry.status
        != JournalEntryStatus.DRAFT
    ):
        raise AccountingPostingError(
            "Only draft journal entries can be posted"
        )

    await ensure_period_open(
        company_id=journal_entry.company_id,
        operation_date=journal_entry.entry_date,
        db=db,
    )

    await validate_journal_entry(
        db=db,
        journal_entry=journal_entry,
    )

    journal_entry.status = (
        JournalEntryStatus.POSTED
    )

    journal_entry.posted_at = datetime.utcnow()

    await db.flush()

    return journal_entry