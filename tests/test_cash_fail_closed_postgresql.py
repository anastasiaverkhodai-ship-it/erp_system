"""Fail-closed cash posting on real isolated PostgreSQL.

Verify that cash posting cannot succeed without an opening
or with unreviewed opening evidence. Rejected posting must
leave Payment in DRAFT and create no JournalEntry.
"""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text

from app.core.database import AsyncSessionLocal
from app.models.account import Account
from app.models.accounting_period import AccountingPeriod
from app.models.cash_desk import CashDesk
from app.models.company import Company
from app.models.counterparty import Counterparty
from app.models.journal_entry import JournalEntry
from app.models.payment import Payment
from app.models.payroll_disbursement import CashDeskOpeningEvidence
from app.models.user import User
from app.services.account_types import (
    AccountType,
    AccountNormalBalance,
)
from app.services.counterparty_types import CounterpartyType
from app.services.cash_physical_posting_enforcement import (
    CashPostingEnforcementError,
)
from app.services.payment_lifecycle_service import (
    confirm_payment,
    create_payment_draft,
)
from app.services.payment_types import (
    PaymentDirection,
    PaymentStatus,
)


@pytest.mark.asyncio
async def test_cash_without_reviewed_opening_is_rejected():
    token = uuid4().hex[:12]
    day = date.today()

    async with AsyncSessionLocal() as setup:
        assert setup.bind.dialect.name == "postgresql"

        dbname = await setup.scalar(
            text("SELECT current_database()")
        )
        assert dbname.startswith("erp_r3_copy_"), (
            "STOP: Disposable PostgreSQL database required"
        )

        # SELECT current_database() started an implicit transaction.
        # End it before opening the fixture write transaction.
        await setup.rollback()
        assert not setup.in_transaction()

        async with setup.begin():
            company = Company(name="Fail Closed " + token)
            setup.add(company)
            await setup.flush()

            actor = User(
                email="cash-fc-" + token + "@example.invalid",
                password_hash="not-used",
                first_name="Cash",
                last_name="Actor",
                is_active=True,
            )
            setup.add(actor)

            counterparty = Counterparty(
                company_id=company.id,
                name="Fail Closed Counterparty " + token,
                counterparty_type=CounterpartyType.BOTH,
                default_currency_code="UAH",
                is_active=True,
            )
            setup.add(counterparty)

            first = day.replace(day=1)
            if day.month == 12:
                next_month = date(day.year + 1, 1, 1)
            else:
                next_month = date(day.year, day.month + 1, 1)

            from datetime import timedelta

            setup.add(AccountingPeriod(
                company_id=company.id,
                year=day.year,
                month=day.month,
                start_date=first,
                end_date=next_month - timedelta(days=1),
                status="open",
                is_locked=False,
            ))

            accounts = [
                Account(
                    company_id=company.id,
                    code=code,
                    name="Fail Closed " + code + " " + token,
                    account_type=kind,
                    normal_balance=normal,
                    is_postable=True,
                    is_system=system,
                    is_active=True,
                )
                for code, kind, normal, system in (
                    (
                        "681", AccountType.LIABILITY,
                        AccountNormalBalance.CREDIT, True,
                    ),
                    (
                        "371", AccountType.ASSET,
                        AccountNormalBalance.DEBIT, True,
                    ),
                    (
                        "301", AccountType.ASSET,
                        AccountNormalBalance.DEBIT, False,
                    ),
                )
            ]
            setup.add_all(accounts)
            await setup.flush()

            cash_account = next(
                account for account in accounts
                if account.code == "301"
            )

            desk = CashDesk(
                company_id=company.id,
                name="Unverified Desk " + token,
                code="FC-" + token,
                currency_code="UAH",
                accounting_account_id=cash_account.id,
                is_active=True,
            )
            setup.add(desk)
            await setup.flush()

            payment = await create_payment_draft(
                setup,
                company_id=company.id,
                counterparty_id=counterparty.id,
                contract_id=None,
                number="FC-OUT-" + token,
                direction=PaymentDirection.OUTGOING,
                payment_date=day,
                currency_code="UAH",
                amount=Decimal("1.00"),
                created_by=actor.id,
                cash_desk_id=desk.id,
            )

            company_id = company.id
            payment_id = payment.id
            actor_id = actor.id
            desk_id = desk.id

    async def attempt_and_verify(stage):
        with pytest.raises(CashPostingEnforcementError):
            async with AsyncSessionLocal() as session:
                async with session.begin():
                    await confirm_payment(
                        session,
                        company_id=company_id,
                        payment_id=payment_id,
                        confirmed_by=actor_id,
                    )

        async with AsyncSessionLocal() as verify:
            row = await verify.get(Payment, payment_id)
            assert row.status == PaymentStatus.DRAFT
            assert row.cash_account_id_snapshot is None

            journal_count = await verify.scalar(
                select(func.count(JournalEntry.id)).where(
                    JournalEntry.company_id == company_id,
                    JournalEntry.payment_id == payment_id,
                )
            )
            assert journal_count == 0

        print(stage + " = REJECTED", flush=True)
        print(stage + " GL LEAK = 0", flush=True)

    await attempt_and_verify("CASH WITHOUT OPENING")

    # An attestation is deliberately NOT independently
    # reviewed. It must not authorize spending.
    async with AsyncSessionLocal() as evidence_session:
        async with evidence_session.begin():
            evidence_session.add(CashDeskOpeningEvidence(
                company_id=company_id,
                cash_desk_id=desk_id,
                effective_date=day.replace(day=1),
                declared_amount=Decimal("100.00"),
                currency_code="UAH",
                evidence_reference="UNREVIEWED-" + token,
                attested_by=actor_id,
            ))

    await attempt_and_verify("CASH UNREVIEWED OPENING")

    print("STRICT CASH POSTING WITHOUT REVIEW = REJECTED", flush=True)
    print("STRICT CASH REJECTED JOURNAL LEAK = 0", flush=True)
    print("STRICT CASH REJECTED PAYMENT STATE = DRAFT", flush=True)
