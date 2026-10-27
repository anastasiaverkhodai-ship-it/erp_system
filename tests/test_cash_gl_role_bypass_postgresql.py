"""Direct cash journals must not bypass reviewed physical cash enforcement.

Run only through scripts/run_postgresql_regression_copy.py. Synthetic records
are rolled back, including when the regression detects a bypass.
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
from app.services.accounting_posting import AccountingPostingError, post_journal_entry
from app.services.cash_physical_posting_enforcement import CashPostingEnforcementError


@pytest.mark.asyncio
@pytest.mark.parametrize('direction', ['outgoing', 'incoming'])
async def test_direct_sql_cash_status_change_is_rejected(direction):
    async with AsyncSessionLocal() as db:
        assert db.bind.dialect.name == 'postgresql'
        name = await db.scalar(text('SELECT current_database()'))
        assert name.startswith('erp_r3_copy_'), 'Disposable PostgreSQL required'
        await db.rollback()
        try:
            token = uuid4().hex[:12]
            company = Company(name='Direct cash audit ' + token)
            actor = User(email=token + '@example.invalid', password_hash='unused',
                         first_name='Audit', last_name='Synthetic', is_active=True)
            db.add_all([company, actor])
            await db.flush()
            day = date(2026, 10, 15)
            db.add(AccountingPeriod(company_id=company.id, year=2026, month=10,
                start_date=date(2026, 10, 1), end_date=date(2026, 10, 31),
                status='open', is_locked=False))
            cash, other = [Account(company_id=company.id, code=code,
                name='Synthetic ' + code, account_type=AccountType.ASSET,
                normal_balance=AccountNormalBalance.DEBIT, is_postable=True,
                is_system=False, is_active=True) for code in ('301', '377')]
            db.add_all([cash, other])
            await db.flush()
            db.add(CashDesk(company_id=company.id, code='AUD-' + token,
                name='Unreviewed audit desk', currency_code='UAH',
                accounting_account_id=cash.id, is_active=True))
            debit, credit = (other, cash) if direction == 'outgoing' else (cash, other)
            entry = JournalEntry(company_id=company.id, entry_date=day,
                description='Direct cash without opening or source',
                status=JournalEntryStatus.DRAFT, created_by=actor.id)
            entry.lines = [
                JournalEntryLine(line_no=1, account_id=debit.id,
                    debit=Decimal('1.00'), credit=Decimal('0.00')),
                JournalEntryLine(line_no=2, account_id=credit.id,
                    debit=Decimal('0.00'), credit=Decimal('1.00')),
            ]
            db.add(entry)
            await db.flush()
            entry_id, company_id = entry.id, company.id
            identity = await db.execute(
                text(
                    "SELECT current_user, "
                    "pg_get_userbyid(c.relowner) "
                    "FROM pg_class c "
                    "WHERE c.oid = 'journal_entries'::regclass"
                )
            )
            runtime_user, table_owner = identity.one()
            print("RUNTIME ROLE =", runtime_user)
            print("TABLE OWNER =", table_owner)

            assert runtime_user == table_owner, (
                "Owner bypass not applicable: runtime is not owner"
            )

            await db.execute(
                text(
                    "ALTER TABLE journal_entries "
                    "DISABLE TRIGGER USER"
                )
            )

            with pytest.raises(DBAPIError):
                async with db.begin_nested():
                    await db.execute(
                        text(
                            "UPDATE journal_entries "
                            "SET status = 'posted', "
                            "posted_at = CURRENT_TIMESTAMP "
                            "WHERE id = :entry_id "
                            "AND company_id = :company_id"
                        ),
                        {
                            "entry_id": entry_id,
                            "company_id": company_id,
                        },
                    )
            await db.refresh(entry)
            assert entry.status == JournalEntryStatus.DRAFT
            assert entry.posted_at is None
        finally:
            await db.rollback()
