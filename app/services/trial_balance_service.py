from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.journal_entry import (
    JournalEntry,
    JournalEntryStatus,
)
from app.models.journal_entry_line import JournalEntryLine
from app.schemas.trial_balance import (
    TrialBalanceLine,
    TrialBalanceReport,
)


ZERO = Decimal("0")

REPORTABLE_STATUSES = (
    JournalEntryStatus.POSTED,
    JournalEntryStatus.REVERSED,
)


def _split_raw_balance(
    raw_balance: Decimal,
) -> tuple[Decimal, Decimal]:
    if raw_balance > ZERO:
        return raw_balance, ZERO

    if raw_balance < ZERO:
        return ZERO, -raw_balance

    return ZERO, ZERO


async def get_trial_balance(
    session: AsyncSession,
    *,
    company_id: int,
    date_from: date,
    date_to: date,
) -> TrialBalanceReport:
    if date_from > date_to:
        raise ValueError(
            "date_from must be less than or equal to date_to"
        )

    opening_debit_expr = func.coalesce(
        func.sum(
            case(
                (
                    JournalEntry.entry_date < date_from,
                    JournalEntryLine.debit,
                ),
                else_=ZERO,
            )
        ),
        ZERO,
    )

    opening_credit_expr = func.coalesce(
        func.sum(
            case(
                (
                    JournalEntry.entry_date < date_from,
                    JournalEntryLine.credit,
                ),
                else_=ZERO,
            )
        ),
        ZERO,
    )

    period_debit_expr = func.coalesce(
        func.sum(
            case(
                (
                    JournalEntry.entry_date.between(
                        date_from,
                        date_to,
                    ),
                    JournalEntryLine.debit,
                ),
                else_=ZERO,
            )
        ),
        ZERO,
    )

    period_credit_expr = func.coalesce(
        func.sum(
            case(
                (
                    JournalEntry.entry_date.between(
                        date_from,
                        date_to,
                    ),
                    JournalEntryLine.credit,
                ),
                else_=ZERO,
            )
        ),
        ZERO,
    )

    statement = (
        select(
            Account.id,
            Account.code,
            Account.name,
            Account.account_type,
            Account.normal_balance,
            opening_debit_expr.label(
                "opening_debit_turnover"
            ),
            opening_credit_expr.label(
                "opening_credit_turnover"
            ),
            period_debit_expr.label(
                "period_debit"
            ),
            period_credit_expr.label(
                "period_credit"
            ),
        )
        .select_from(JournalEntryLine)
        .join(
            JournalEntry,
            JournalEntry.id
            == JournalEntryLine.journal_entry_id,
        )
        .join(
            Account,
            Account.id
            == JournalEntryLine.account_id,
        )
        .where(
            JournalEntry.company_id == company_id,
            Account.company_id == company_id,
            JournalEntry.status.in_(
                REPORTABLE_STATUSES
            ),
            JournalEntry.entry_date <= date_to,
        )
        .group_by(
            Account.id,
            Account.code,
            Account.name,
            Account.account_type,
            Account.normal_balance,
        )
        .order_by(
            Account.code,
            Account.id,
        )
    )

    rows = (
        await session.execute(statement)
    ).all()

    lines: list[TrialBalanceLine] = []

    total_opening_debit = ZERO
    total_opening_credit = ZERO
    total_period_debit = ZERO
    total_period_credit = ZERO
    total_closing_debit = ZERO
    total_closing_credit = ZERO

    for row in rows:
        opening_raw = (
            Decimal(row.opening_debit_turnover)
            - Decimal(row.opening_credit_turnover)
        )

        period_debit = Decimal(
            row.period_debit
        )
        period_credit = Decimal(
            row.period_credit
        )

        closing_raw = (
            opening_raw
            + period_debit
            - period_credit
        )

        (
            opening_debit,
            opening_credit,
        ) = _split_raw_balance(opening_raw)

        (
            closing_debit,
            closing_credit,
        ) = _split_raw_balance(closing_raw)

        line = TrialBalanceLine(
            account_id=row.id,
            account_code=row.code,
            account_name=row.name,
            account_type=row.account_type.value,
            normal_balance=row.normal_balance.value,
            opening_debit=opening_debit,
            opening_credit=opening_credit,
            period_debit=period_debit,
            period_credit=period_credit,
            closing_debit=closing_debit,
            closing_credit=closing_credit,
        )

        lines.append(line)

        total_opening_debit += opening_debit
        total_opening_credit += opening_credit

        total_period_debit += period_debit
        total_period_credit += period_credit

        total_closing_debit += closing_debit
        total_closing_credit += closing_credit

    return TrialBalanceReport(
        company_id=company_id,
        date_from=date_from,
        date_to=date_to,
        lines=lines,
        total_opening_debit=total_opening_debit,
        total_opening_credit=total_opening_credit,
        total_period_debit=total_period_debit,
        total_period_credit=total_period_credit,
        total_closing_debit=total_closing_debit,
        total_closing_credit=total_closing_credit,
    )
