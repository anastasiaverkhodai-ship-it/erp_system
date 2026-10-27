"""Security E2E: finalized GL mutation and forged reversal.

Requires disposable PostgreSQL clone with migration c056.
"""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.core.database import AsyncSessionLocal
from app.models.account import Account
from app.models.accounting_period import AccountingPeriod
from app.models.cash_desk import CashDesk
from app.models.company import Company
from app.models.journal_entry import JournalEntry, JournalEntryStatus
from app.models.journal_entry_line import JournalEntryLine
from app.models.user import User
from app.services.account_types import AccountType, AccountNormalBalance
from app.services.accounting_posting import post_journal_entry


@pytest.mark.asyncio
@pytest.mark.parametrize("direction", ("outgoing", "incoming"))
async def test_posted_reversal_rejects_late_cash_line(direction):
    async with AsyncSessionLocal() as db:
        assert db.bind.dialect.name == "postgresql"
        dbname = await db.scalar(text("SELECT current_database()"))
        assert dbname.startswith("erp_r3_copy_"), (
            "Disposable PostgreSQL copy required"
        )
        await db.rollback()

        try:
            token = uuid4().hex[:12]
            company = Company(name="Finalized GL security " + token)
            actor = User(
                email=token + "@example.invalid",
                password_hash="unused",
                first_name="Security",
                last_name="Test",
                is_active=True,
            )
            db.add_all((company, actor))
            await db.flush()

            day = date(2026, 10, 15)
            db.add(AccountingPeriod(
                company_id=company.id,
                year=2026,
                month=10,
                start_date=date(2026, 10, 1),
                end_date=date(2026, 10, 31),
                status="open",
                is_locked=False,
            ))

            cash, other_a, other_b = [
                Account(
                    company_id=company.id,
                    code=code,
                    name="Security account " + code,
                    account_type=AccountType.ASSET,
                    normal_balance=AccountNormalBalance.DEBIT,
                    is_postable=True,
                    is_system=False,
                    is_active=True,
                )
                for code in ("301", "377", "378")
            ]
            db.add_all((cash, other_a, other_b))
            await db.flush()

            db.add(CashDesk(
                company_id=company.id,
                code="SEC-" + token,
                name="Security cash desk",
                currency_code="UAH",
                accounting_account_id=cash.id,
                is_active=True,
            ))
            await db.flush()

            debit, credit = (
                (other_a, other_b)
                if direction == "outgoing"
                else (other_b, other_a)
            )

            entry = JournalEntry(
                company_id=company.id,
                entry_date=day,
                description="Legitimate noncash finalized journal",
                status=JournalEntryStatus.DRAFT,
                created_by=actor.id,
            )
            entry.lines = [
                JournalEntryLine(
                    line_no=1,
                    account_id=debit.id,
                    debit=Decimal("1.00"),
                    credit=Decimal("0.00"),
                ),
                JournalEntryLine(
                    line_no=2,
                    account_id=credit.id,
                    debit=Decimal("0.00"),
                    credit=Decimal("1.00"),
                ),
            ]
            db.add(entry)
            await db.flush()

            await post_journal_entry(db, company.id, entry.id)
            assert entry.status == JournalEntryStatus.POSTED

            from app.services.accounting_reversal import (
                reverse_journal_entry,
            )

            reversal = await reverse_journal_entry(
                db=db,
                company_id=company.id,
                journal_entry_id=entry.id,
                reversal_date=day,
                reversed_by=actor.id,
            )
            await db.flush()

            assert reversal.status == JournalEntryStatus.POSTED
            assert entry.status == JournalEntryStatus.REVERSED

            with pytest.raises(DBAPIError):
                async with db.begin_nested():
                    await db.execute(
                        text("""
                            INSERT INTO journal_entry_lines (
                                journal_entry_id,
                                line_no,
                                account_id,
                                debit,
                                credit
                            ) VALUES (
                                :journal_id, 99, :cash_id,
                                1.00, 0.00
                            )
                        """),
                        {
                            "journal_id": reversal.id,
                            "cash_id": cash.id,
                        },
                    )
                    await db.execute(
                        text(
                            "SET CONSTRAINTS "
                            "trg_reversal_line_seal IMMEDIATE"
                        )
                    )

            count = await db.scalar(
                text("""
                    SELECT COUNT(*)
                    FROM journal_entry_lines
                    WHERE journal_entry_id = :id
                """),
                {"id": reversal.id},
            )
            assert count == 2

        finally:
            await db.rollback()
