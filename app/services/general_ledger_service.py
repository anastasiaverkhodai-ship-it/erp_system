from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.journal_entry import JournalEntry, JournalEntryStatus
from app.models.journal_entry_line import JournalEntryLine
from app.schemas.general_ledger import (
    AccountCardReport,
    GeneralLedgerLine,
    GeneralLedgerReport,
)


ZERO = Decimal("0")

REPORTABLE_STATUSES = (
    JournalEntryStatus.POSTED,
    JournalEntryStatus.REVERSED,
)


def _movement_expression():
    return JournalEntryLine.debit - JournalEntryLine.credit


def _base_statement(
    *,
    company_id: int,
    account_id: int | None = None,
    account_code: str | None = None,
) -> Select:
    stmt = (
        select(
            JournalEntry,
            JournalEntryLine,
            Account,
        )
        .join(
            JournalEntryLine,
            JournalEntryLine.journal_entry_id == JournalEntry.id,
        )
        .join(
            Account,
            Account.id == JournalEntryLine.account_id,
        )
        .where(
            JournalEntry.company_id == company_id,
            Account.company_id == company_id,
            JournalEntry.status.in_(REPORTABLE_STATUSES),
        )
    )

    if account_id is not None:
        stmt = stmt.where(Account.id == account_id)

    if account_code is not None:
        stmt = stmt.where(Account.code == account_code)

    return stmt


async def _opening_balance(
    session: AsyncSession,
    *,
    company_id: int,
    date_from: date,
    account_id: int | None = None,
    account_code: str | None = None,
) -> Decimal:
    stmt = (
        select(
            func.coalesce(
                func.sum(_movement_expression()),
                ZERO,
            )
        )
        .select_from(JournalEntryLine)
        .join(
            JournalEntry,
            JournalEntry.id == JournalEntryLine.journal_entry_id,
        )
        .join(
            Account,
            Account.id == JournalEntryLine.account_id,
        )
        .where(
            JournalEntry.company_id == company_id,
            Account.company_id == company_id,
            JournalEntry.status.in_(REPORTABLE_STATUSES),
            JournalEntry.entry_date < date_from,
        )
    )

    if account_id is not None:
        stmt = stmt.where(Account.id == account_id)

    if account_code is not None:
        stmt = stmt.where(Account.code == account_code)

    value = (await session.execute(stmt)).scalar_one()

    return Decimal(value or ZERO)


async def get_general_ledger(
    session: AsyncSession,
    *,
    company_id: int,
    date_from: date,
    date_to: date,
    account_id: int | None = None,
    account_code: str | None = None,
) -> GeneralLedgerReport:
    if date_from > date_to:
        raise ValueError("date_from must be on or before date_to")

    if account_id is not None and account_code is not None:
        raise ValueError(
            "Use either account_id or account_code, not both"
        )

    opening = await _opening_balance(
        session,
        company_id=company_id,
        date_from=date_from,
        account_id=account_id,
        account_code=account_code,
    )

    stmt = (
        _base_statement(
            company_id=company_id,
            account_id=account_id,
            account_code=account_code,
        )
        .where(
            JournalEntry.entry_date >= date_from,
            JournalEntry.entry_date <= date_to,
        )
        .order_by(
            JournalEntry.entry_date,
            JournalEntry.id,
            JournalEntryLine.line_no,
            JournalEntryLine.id,
        )
    )

    rows = (await session.execute(stmt.execution_options(populate_existing=True))).all()

    running = opening
    period_debit = ZERO
    period_credit = ZERO
    lines: list[GeneralLedgerLine] = []

    for journal_entry, journal_line, account in rows:
        debit = Decimal(journal_line.debit or ZERO)
        credit = Decimal(journal_line.credit or ZERO)

        period_debit += debit
        period_credit += credit
        running += debit - credit

        description = getattr(
            journal_line,
            "description",
            None,
        )

        if not description:
            description = journal_entry.description

        lines.append(
            GeneralLedgerLine(
                journal_entry_id=journal_entry.id,
                journal_entry_line_id=journal_line.id,
                entry_date=journal_entry.entry_date,
                line_no=journal_line.line_no,
                account_id=account.id,
                account_code=account.code,
                account_name=account.name,
                description=description,
                debit=debit,
                credit=credit,
                running_balance=running,
            )
        )

    return GeneralLedgerReport(
        company_id=company_id,
        date_from=date_from,
        date_to=date_to,
        account_id=account_id,
        account_code=account_code,
        opening_balance=opening,
        period_debit=period_debit,
        period_credit=period_credit,
        closing_balance=running,
        lines=lines,
    )


async def get_account_card(
    session: AsyncSession,
    *,
    company_id: int,
    account_id: int,
    date_from: date,
    date_to: date,
) -> AccountCardReport:
    if date_from > date_to:
        raise ValueError("date_from must be on or before date_to")

    account = (
        await session.execute(
            select(Account).where(
                Account.id == account_id,
                Account.company_id == company_id,
            ).execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()

    if account is None:
        raise ValueError("Account not found for company")

    report = await get_general_ledger(
        session,
        company_id=company_id,
        date_from=date_from,
        date_to=date_to,
        account_id=account_id,
    )

    normal_balance = getattr(
        account.normal_balance,
        "value",
        str(account.normal_balance),
    )

    payload = report.model_dump()
    payload.update(
        account_id=account.id,
        account_code=account.code,
        account_name=account.name,
        normal_balance=normal_balance,
    )

    return AccountCardReport(**payload)
