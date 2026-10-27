"""Posted journal SQL mutation and runtime role audit.

Only execute on the disposable PostgreSQL regression copy.
"""

from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.core.database import AsyncSessionLocal
from app.models.account import Account
from app.models.accounting_period import AccountingPeriod
from app.models.company import Company
from app.models.journal_entry import JournalEntry, JournalEntryStatus
from app.models.journal_entry_line import JournalEntryLine
from app.models.user import User
from app.services.account_types import AccountType, AccountNormalBalance
from app.services.accounting_posting import post_journal_entry


@pytest.mark.asyncio
@pytest.mark.parametrize("direction", ("outgoing", "incoming"))
@pytest.mark.parametrize(
    "attack",
    ("reset_status", "change_posting_date", "erase_posted_at"),
)
async def test_finalized_journal_header_is_immutable(direction, attack):
    async with AsyncSessionLocal() as db:
        assert db.bind.dialect.name == "postgresql"
        dbname = await db.scalar(text("SELECT current_database()"))
        assert dbname.startswith("erp_r3_copy_")
        await db.rollback()

        try:
            token = uuid4().hex[:12]
            company = Company(name="Posted header audit " + token)
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

            a, b = [
                Account(
                    company_id=company.id,
                    code=code,
                    name="Audit account " + code,
                    account_type=AccountType.ASSET,
                    normal_balance=AccountNormalBalance.DEBIT,
                    is_postable=True,
                    is_system=False,
                    is_active=True,
                )
                for code in ("377", "378")
            ]
            db.add_all([a, b])
            await db.flush()

            debit, credit = (
                (a, b) if direction == "outgoing" else (b, a)
            )

            entry = JournalEntry(
                company_id=company.id,
                entry_date=day,
                description="Legitimate finalized journal",
                status=JournalEntryStatus.DRAFT,
                created_by=actor.id,
            )
            entry.lines = [
                JournalEntryLine(
                    line_no=1,
                    account_id=debit.id,
                    debit=Decimal("10.00"),
                    credit=Decimal("0.00"),
                ),
                JournalEntryLine(
                    line_no=2,
                    account_id=credit.id,
                    debit=Decimal("0.00"),
                    credit=Decimal("10.00"),
                ),
            ]
            db.add(entry)
            await db.flush()
            await post_journal_entry(db, company.id, entry.id)
            await db.refresh(entry)
            assert entry.status == JournalEntryStatus.POSTED
            assert entry.posted_at is not None

            mutations = {
                "reset_status": (
                    "UPDATE journal_entries SET status = 'draft' "
                    "WHERE id = :id"
                ),
                "change_posting_date": (
                    "UPDATE journal_entries "
                    "SET entry_date = :new_date WHERE id = :id"
                ),
                "erase_posted_at": (
                    "UPDATE journal_entries SET posted_at = NULL "
                    "WHERE id = :id"
                ),
            }

            with pytest.raises(DBAPIError):
                async with db.begin_nested():
                    await db.execute(
                        text(mutations[attack]),
                        {
                            "id": entry.id,
                            "new_date": day + timedelta(days=1),
                        },
                    )

            await db.refresh(entry)
            assert entry.status == JournalEntryStatus.POSTED
            assert entry.entry_date == day
            assert entry.posted_at is not None

        finally:
            await db.rollback()


@pytest.mark.asyncio
async def test_runtime_database_role_audit():
    async with AsyncSessionLocal() as db:
        assert db.bind.dialect.name == "postgresql"
        dbname = await db.scalar(text("SELECT current_database()"))
        assert dbname.startswith("erp_r3_copy_")

        result = await db.execute(text("""
            SELECT
                current_user,
                current_setting('is_superuser'),
                pg_get_userbyid(c.relowner) AS journal_owner,
                has_table_privilege(
                    current_user, 'journal_entries', 'UPDATE'
                ) AS journal_update,
                has_table_privilege(
                    current_user, 'journal_entry_lines', 'DELETE'
                ) AS line_delete,
                has_table_privilege(
                    current_user, 'journal_entries', 'TRIGGER'
                ) AS journal_trigger
            FROM pg_class c
            WHERE c.oid = 'journal_entries'::regclass
        """))
        row = result.one()

        print("ROLE AUDIT USER =", row[0])
        print("ROLE AUDIT SUPERUSER =", row[1])
        print("ROLE AUDIT JOURNAL OWNER =", row[2])
        print("ROLE AUDIT JOURNAL UPDATE =", row[3])
        print("ROLE AUDIT LINE DELETE =", row[4])
        print("ROLE AUDIT TRIGGER PRIVILEGE =", row[5])

        assert row[0]
        await db.rollback()
