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
@pytest.mark.parametrize(
    "attack",
    ("inject_cash_line", "delete_finalized_line", "forge_reversal"),
)
async def test_finalized_gl_rejects_direct_sql_attack(direction, attack):
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

            if attack == "inject_cash_line":
                statement = text("""
                    UPDATE journal_entry_lines
                    SET account_id = :cash_id
                    WHERE journal_entry_id = :entry_id
                      AND line_no = 1
                """)
                params = {
                    "cash_id": cash.id,
                    "entry_id": entry.id,
                }

            elif attack == "delete_finalized_line":
                statement = text("""
                    DELETE FROM journal_entry_lines
                    WHERE journal_entry_id = :entry_id
                      AND line_no = 1
                """)
                params = {"entry_id": entry.id}

            else:
                statement = text("""
                    INSERT INTO journal_entries (
                        company_id, entry_date, description,
                        status, created_by, created_at,
                        posted_at, reversal_of_id
                    ) VALUES (
                        :company_id, :entry_date, :description,
                        'posted', :created_by, CURRENT_TIMESTAMP,
                        CURRENT_TIMESTAMP, :original_id
                    )
                """)
                params = {
                    "company_id": company.id,
                    "entry_date": day,
                    "description": "Forged SQL reversal",
                    "created_by": actor.id,
                    "original_id": entry.id,
                }

            with pytest.raises(DBAPIError):
                async with db.begin_nested():
                    await db.execute(statement, params)
                    if attack == "forge_reversal":
                        await db.execute(
                            text(
                                "SET CONSTRAINTS "
                                "trg_cash_reversal_integrity IMMEDIATE"
                            )
                        )

            await db.refresh(entry)
            assert entry.status == JournalEntryStatus.POSTED

        finally:
            await db.rollback()
