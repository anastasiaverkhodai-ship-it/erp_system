from datetime import datetime, timezone
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
    from app.services.payroll_mutation_guard import (
        lock_payroll_journal_company, require_posted_payroll_accrual,
    )
    await lock_payroll_journal_company(db, company_id, journal_entry_id)
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
        .execution_options(populate_existing=True)
    )

    journal_entry = result.scalar_one_or_none()

    if journal_entry is None:
        raise JournalEntryNotFoundError(
            "Journal entry not found"
        )

    if journal_entry.payroll_disbursement_id is not None:
        from app.models.payroll_disbursement import PayrollDisbursement
        disbursement = await db.scalar(select(PayrollDisbursement).where(
            PayrollDisbursement.company_id == company_id,
            PayrollDisbursement.id == journal_entry.payroll_disbursement_id,
        ))
        if disbursement is None:
            raise AccountingPostingError('Payroll disbursement not found')
        await require_posted_payroll_accrual(db, company_id=company_id,
            disbursement=disbursement, error_type=AccountingPostingError)

    commissioning_id = getattr(journal_entry, "fixed_asset_commissioning_id", None)
    if commissioning_id is not None and db.info.get("fixed_asset_commissioning") != commissioning_id:
        raise AccountingPostingError("Use the fixed asset commissioning lifecycle")

    depreciation_id = getattr(journal_entry, "fixed_asset_depreciation_id", None)
    if depreciation_id is not None and db.info.get("fixed_asset_depreciation") != depreciation_id:
        raise AccountingPostingError("Use the fixed asset depreciation lifecycle")

    valuation_id = getattr(
        journal_entry,
        "fixed_asset_revaluation_impairment_id",
        None,
    )
    if (
        valuation_id is not None
        and db.info.get("fixed_asset_revaluation_impairment")
        != valuation_id
    ):
        raise AccountingPostingError(
            "Use the fixed asset revaluation/impairment lifecycle"
        )

    repair_improvement_id = getattr(
        journal_entry,
        "fixed_asset_repair_improvement_id",
        None,
    )
    if (
        repair_improvement_id is not None
        and db.info.get("fixed_asset_repair_improvement")
        != repair_improvement_id
    ):
        raise AccountingPostingError(
            "Use the fixed asset repair/improvement lifecycle"
        )

    disposal_id = getattr(
        journal_entry,
        "fixed_asset_disposal_id",
        None,
    )
    if (
        disposal_id is not None
        and db.info.get("fixed_asset_disposal")
        != disposal_id
    ):
        raise AccountingPostingError(
            "Use the fixed asset disposal lifecycle"
        )


    closing_id = getattr(journal_entry, "year_end_closing_id", None)
    if closing_id is not None and db.info.get("year_end_closing_active") != closing_id:
        raise AccountingPostingError("Post year-end journals through their closing lifecycle")

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
    if journal_entry.payroll_disbursement_id is not None:
        disbursement.confirmed_at = journal_entry.posted_at.replace(tzinfo=timezone.utc)

    await db.flush()

    return journal_entry