"""Real PostgreSQL cash competition and historical-overdraft E2E.

Runs only through the isolated PostgreSQL regression runner.
No mocks. Two distinct sessions compete for the same CashDesk row.
"""

import asyncio
import calendar
import os
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import func, select

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
from app.services.account_types import AccountType, AccountNormalBalance
from app.services.counterparty_types import CounterpartyType
from app.services.cash_desk_opening_review_service import (
    record_independent_cash_opening_review,
)
from app.services.cash_physical_posting_enforcement import (
    CashPostingEnforcementError,
)
from app.services.cash_physical_balance_guard import (
    validate_locked_physical_cash,
)
from app.services.payment_lifecycle_service import (
    create_payment_draft,
    confirm_payment,
)
from app.services.payment_types import (
    PaymentDirection,
    PaymentStatus,
)


@pytest.mark.asyncio
async def test_same_desk_concurrent_outgoing_and_backdated_overdraft():
    assert os.getenv("RUN_POSTGRES_E2E") == "1", (
        "STOP: Real isolated PostgreSQL E2E required"
    )

    today = date.today()
    opening_day = today.replace(day=1)
    first_day = today.replace(day=11)
    spending_day = today.replace(day=12)
    income_day = today.replace(day=13)
    end_day = today.replace(
        day=calendar.monthrange(today.year, today.month)[1]
    )

    token = uuid4().hex[:12]

    # Establish independently reviewed opening and two DRAFT
    # payment headers in one committed setup transaction.
    async with AsyncSessionLocal() as setup:
        assert setup.bind.dialect.name == "postgresql"

        async with setup.begin():
            company = Company(name="Cash Concurrency " + token)
            setup.add(company)
            await setup.flush()

            actor = User(
                email=f"cash-{token}@example.invalid",
                password_hash="not-used",
                first_name="Cash",
                last_name="Actor",
                is_active=True,
            )
            reviewer = User(
                email=f"cash-review-{token}@example.invalid",
                password_hash="not-used",
                first_name="Independent",
                last_name="Reviewer",
                is_active=True,
            )
            setup.add_all([actor, reviewer])
            await setup.flush()
            assert actor.id != reviewer.id

            counterparty = Counterparty(
                company_id=company.id,
                name="Cash Counterparty " + token,
                counterparty_type=CounterpartyType.BOTH,
                default_currency_code="UAH",
                is_active=True,
            )
            setup.add(counterparty)

            setup.add(
                AccountingPeriod(
                    company_id=company.id,
                    year=today.year,
                    month=today.month,
                    start_date=opening_day,
                    end_date=end_day,
                    status="open",
                    is_locked=False,
                )
            )

            accounts = [
                Account(
                    company_id=company.id,
                    code=code,
                    name=f"{token} {name}",
                    account_type=kind,
                    normal_balance=normal,
                    is_postable=True,
                    is_system=system,
                    is_active=True,
                )
                for code, name, kind, normal, system in (
                    (
                        "681", "Customer advances",
                        AccountType.LIABILITY,
                        AccountNormalBalance.CREDIT, True,
                    ),
                    (
                        "371", "Supplier advances",
                        AccountType.ASSET,
                        AccountNormalBalance.DEBIT, True,
                    ),
                    (
                        "301", "Physical cash",
                        AccountType.ASSET,
                        AccountNormalBalance.DEBIT, False,
                    ),
                )
            ]
            setup.add_all(accounts)
            await setup.flush()

            cash_account = next(
                row for row in accounts if row.code == "301"
            )

            desk = CashDesk(
                company_id=company.id,
                name="Concurrency cash desk " + token,
                code="CC-" + token,
                currency_code="UAH",
                accounting_account_id=cash_account.id,
                is_active=True,
            )
            setup.add(desk)
            await setup.flush()

            opening = CashDeskOpeningEvidence(
                company_id=company.id,
                cash_desk_id=desk.id,
                effective_date=opening_day,
                declared_amount=Decimal("100.00"),
                currency_code="UAH",
                evidence_reference="E2E-COUNT-" + token,
                attested_by=actor.id,
            )
            setup.add(opening)
            await setup.flush()

            review = await record_independent_cash_opening_review(
                setup,
                company_id=company.id,
                opening_evidence_id=opening.id,
                reviewed_by=reviewer.id,
                review_reference="E2E-REVIEW-" + token,
            )
            assert review.id is not None

            async def draft(number, direction, amount, day):
                return await create_payment_draft(
                    setup,
                    company_id=company.id,
                    counterparty_id=counterparty.id,
                    contract_id=None,
                    number=number + "-" + token,
                    direction=direction,
                    payment_date=day,
                    currency_code="UAH",
                    amount=Decimal(amount),
                    created_by=actor.id,
                    cash_desk_id=desk.id,
                )

            first = await draft(
                "CONCURRENT-A", PaymentDirection.OUTGOING,
                "70.00", spending_day,
            )
            second = await draft(
                "CONCURRENT-B", PaymentDirection.OUTGOING,
                "70.00", spending_day,
            )
            future_income = await draft(
                "LATER-IN", PaymentDirection.INCOMING,
                "100.00", income_day,
            )
            backdated = await draft(
                "BACKDATED-OUT", PaymentDirection.OUTGOING,
                "40.00", first_day,
            )

            company_id = company.id
            actor_id = actor.id
            desk_id = desk.id
            first_id = first.id
            second_id = second.id
            income_id = future_income.id
            backdated_id = backdated.id

    print("REVIEWED OPENING = 100.00 UAH", flush=True)
    print("TWO OUTGOING REQUESTS = 70.00 + 70.00", flush=True)

    async def second_writer():
        async with AsyncSessionLocal() as session:
            async with session.begin():
                return await confirm_payment(
                    session,
                    company_id=company_id,
                    payment_id=second_id,
                    confirmed_by=actor_id,
                )

    # Session 1 posts 70 but intentionally holds its
    # transaction open. Session 2 must block on the SAME
    # cash desk rather than read the old 100 balance.
    async with AsyncSessionLocal() as session1:
        async with session1.begin():
            await confirm_payment(
                session1,
                company_id=company_id,
                payment_id=first_id,
                confirmed_by=actor_id,
            )
            await session1.flush()

            task = asyncio.create_task(second_writer())

            try:
                # The second writer must still be waiting while
                # transaction 1 owns the CashDesk row lock.
                await asyncio.sleep(0.35)
                assert not task.done(), (
                    "STOP: Second writer did not wait for "
                    "the first cash transaction"
                )
                print(
                    "SECOND TRANSACTION WAITS FOR DESK LOCK = PASS",
                    flush=True,
                )
            except BaseException:
                task.cancel()
                await asyncio.gather(
                    task, return_exceptions=True
                )
                raise

        # Exiting transaction 1 commits the first withdrawal.

    print("FIRST WITHDRAWAL COMMITTED = PASS", flush=True)

    # The second writer is released and must see 30 remaining.
    # Its attempt to withdraw another 70 must be rejected.
    with pytest.raises(CashPostingEnforcementError):
        await asyncio.wait_for(task, timeout=25)

    print(
        "CONCURRENT SECOND OVERDRAFT REJECTED = PASS",
        flush=True,
    )

    # Failure must leave the second payment DRAFT with no
    # journal and no historical cash snapshot.
    async with AsyncSessionLocal() as verify:
        first_row = await verify.get(Payment, first_id)
        second_row = await verify.get(Payment, second_id)

        assert first_row.status == PaymentStatus.CONFIRMED
        assert second_row.status == PaymentStatus.DRAFT
        assert second_row.cash_account_id_snapshot is None

        first_journal_count = await verify.scalar(
            select(func.count(JournalEntry.id)).where(
                JournalEntry.company_id == company_id,
                JournalEntry.payment_id == first_id,
            )
        )
        second_journal_count = await verify.scalar(
            select(func.count(JournalEntry.id)).where(
                JournalEntry.company_id == company_id,
                JournalEntry.payment_id == second_id,
            )
        )

        assert first_journal_count == 1
        assert second_journal_count == 0

    print("REJECTED CONCURRENT PAYMENT GL LEAK = 0", flush=True)
    print("REJECTED CONCURRENT SNAPSHOT LEAK = 0", flush=True)

    # Later income makes the final balance positive, but must
    # not retroactively finance an earlier overdraft.
    async with AsyncSessionLocal() as income_session:
        async with income_session.begin():
            await confirm_payment(
                income_session,
                company_id=company_id,
                payment_id=income_id,
                confirmed_by=actor_id,
            )

    # Chronology:
    # opening day: 100
    # day 11 proposed withdrawal: -40 => 60
    # day 12 existing withdrawal: -70 => -10 (INVALID)
    # day 13 later income: +100 => 90 (cannot repair day 12)
    with pytest.raises(CashPostingEnforcementError):
        async with AsyncSessionLocal() as backdated_session:
            async with backdated_session.begin():
                await confirm_payment(
                    backdated_session,
                    company_id=company_id,
                    payment_id=backdated_id,
                    confirmed_by=actor_id,
                )

    print("BACKDATED INTERMEDIATE OVERDRAFT = REJECTED", flush=True)

    async with AsyncSessionLocal() as final_session:
        backdated_row = await final_session.get(
            Payment, backdated_id
        )
        assert backdated_row.status == PaymentStatus.DRAFT
        assert backdated_row.cash_account_id_snapshot is None

        count = await final_session.scalar(
            select(func.count(JournalEntry.id)).where(
                JournalEntry.company_id == company_id,
                JournalEntry.payment_id == backdated_id,
            )
        )
        assert count == 0

        result = await validate_locked_physical_cash(
            final_session,
            company_id=company_id,
            cash_desk_id=desk_id,
            as_of_date=income_day,
        )
        assert result.closing_balance == Decimal("130.00")

    print("BACKDATED FAILURE ROLLBACK = PASS", flush=True)
    print("BACKDATED GL LEAK = 0", flush=True)
    print("FINAL CASH BALANCE = 130.00 UAH", flush=True)
    print("REAL TWO-SESSION CASH CONCURRENCY = PASS", flush=True)
    print("REAL BACKDATED CASH OVERDRAFT = PASS", flush=True)
