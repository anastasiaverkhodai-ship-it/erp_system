"""Negative security probes for direct PostgreSQL cash GL writes.

Run only through scripts.run_postgresql_regression_copy.
No source PostgreSQL writes are permitted.
"""

from datetime import date, datetime
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
from app.services.account_types import (
    AccountType,
    AccountNormalBalance,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("direction", ("outgoing", "incoming"))
@pytest.mark.parametrize(
    "attack",
    ("insert_posted", "update_posted_line", "delete_posted_line"),
)
async def test_direct_cash_sql_write_must_be_rejected(direction, attack):
    async with AsyncSessionLocal() as db:
        assert db.bind.dialect.name == "postgresql"
        database = await db.scalar(text("SELECT current_database()"))
        assert database.startswith("erp_r3_copy_"), (
            "Disposable PostgreSQL copy required"
        )
        await db.rollback()

        try:
            token = uuid4().hex[:12]
            company = Company(name="Cash write security " + token)
            actor = User(
                email=token + "@example.invalid",
                password_hash="unused",
                first_name="Audit",
                last_name="Synthetic",
                is_active=True,
            )
            db.add_all([company, actor])
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

            cash, other = [
                Account(
                    company_id=company.id,
                    code=code,
                    name="Synthetic " + code,
                    account_type=AccountType.ASSET,
                    normal_balance=AccountNormalBalance.DEBIT,
                    is_postable=True,
                    is_system=False,
                    is_active=True,
                )
                for code in ("301", "377")
            ]
            db.add_all([cash, other])
            await db.flush()

            db.add(CashDesk(
                company_id=company.id,
                code="SEC-" + token,
                name="Security probe desk",
                currency_code="UAH",
                accounting_account_id=cash.id,
                is_active=True,
            ))
            await db.flush()

            debit_account, credit_account = (
                (other, cash)
                if direction == "outgoing"
                else (cash, other)
            )

            if attack == "insert_posted":
                # This bypasses the DRAFT -> POSTED UPDATE trigger.
                # A posted header with no lines is not a valid journal.
                with pytest.raises(DBAPIError):
                    async with db.begin_nested():
                        await db.execute(
                            text("""
                                INSERT INTO journal_entries (
                                    company_id, entry_date,
                                    description, status, created_by,
                                    created_at, posted_at
                                )
                                VALUES (
                                    :company_id, :entry_date,
                                    :description, 'posted', :actor_id,
                                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                                )
                            """),
                            {
                                "company_id": company.id,
                                "entry_date": day,
                                "description": "Illegal direct posted insert",
                                "actor_id": actor.id,
                            },
                        )
                return

            noncash = Account(
                company_id=company.id,
                code="378",
                name="Synthetic noncash security account",
                account_type=AccountType.ASSET,
                normal_balance=AccountNormalBalance.DEBIT,
                is_postable=True,
                is_system=False,
                is_active=True,
            )
            db.add(noncash)
            await db.flush()
            debit_account, credit_account = (
                (other, noncash)
                if direction == "outgoing"
                else (noncash, other)
            )

            entry = JournalEntry(
                company_id=company.id,
                entry_date=day,
                description="Synthetic posted cash mutation target",
                status=JournalEntryStatus.DRAFT,
                created_by=actor.id,
            )
            entry.lines = [
                JournalEntryLine(
                    line_no=1,
                    account_id=debit_account.id,
                    debit=Decimal("1.00"),
                    credit=Decimal("0.00"),
                ),
                JournalEntryLine(
                    line_no=2,
                    account_id=credit_account.id,
                    debit=Decimal("0.00"),
                    credit=Decimal("1.00"),
                ),
            ]
            db.add(entry)
            await db.flush()
            from app.services.accounting_posting import post_journal_entry
            await post_journal_entry(db, company.id, entry.id)
            await db.refresh(entry)
            assert entry.status == JournalEntryStatus.POSTED

            protected_line = entry.lines[0]
            assert protected_line.id is not None

            if attack == "update_posted_line":
                # A direct SQL client attempts to change posted money.
                sql = text("""
                    UPDATE journal_entry_lines
                    SET debit = CASE WHEN debit > 0
                        THEN debit + 1 ELSE debit END,
                        credit = CASE WHEN credit > 0
                        THEN credit + 1 ELSE credit END
                    WHERE id = :line_id
                """)
            else:
                sql = text("""
                    DELETE FROM journal_entry_lines
                    WHERE id = :line_id
                """)

            with pytest.raises(DBAPIError):
                async with db.begin_nested():
                    await db.execute(
                        sql, {"line_id": protected_line.id}
                    )
        finally:
            await db.rollback()
