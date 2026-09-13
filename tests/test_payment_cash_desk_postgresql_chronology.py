import os
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import AsyncSessionLocal
from app.models.account import Account
from app.models.cash_desk import CashDesk
from app.models.company import Company
from app.models.counterparty import Counterparty
from app.models.journal_entry import (
    JournalEntry,
    JournalEntryStatus,
)
from app.models.payment import Payment
from app.models.user import User
from app.services.account_types import (
    AccountNormalBalance,
    AccountType,
)
from app.services.cash_desk_service import (
    update_cash_desk,
)
from app.services.counterparty_types import (
    CounterpartyType,
)
from app.services.payment_lifecycle_service import (
    PaymentStatusError,
    cancel_payment,
    confirm_payment,
    create_payment_draft,
)
from app.services.payment_types import (
    PaymentDirection,
    PaymentStatus,
)


RUN_POSTGRES_E2E = (
    os.getenv("RUN_POSTGRES_E2E", "").strip() == "1"
)


async def _count(
    db: AsyncSession,
    model,
    *criteria,
) -> int:
    stmt = (
        select(func.count())
        .select_from(model)
    )

    if criteria:
        stmt = stmt.where(*criteria)

    return int(
        (await db.execute(stmt)).scalar_one()
    )


async def _payment_entries(
    db: AsyncSession,
    *,
    company_id: int,
    payment_id: int,
):
    return tuple(
        (
            await db.execute(
                select(JournalEntry)
                .options(
                    selectinload(
                        JournalEntry.lines
                    )
                )
                .where(
                    JournalEntry.company_id
                    == company_id,
                    JournalEntry.payment_id
                    == payment_id,
                )
                .order_by(JournalEntry.id)
            )
        )
        .scalars()
        .all()
    )


def _line_by_account(entry, account_id: int):
    matches = [
        line
        for line in entry.lines
        if line.account_id == account_id
    ]

    assert len(matches) == 1

    return matches[0]


@pytest.mark.skipif(
    not RUN_POSTGRES_E2E,
    reason=(
        "Set RUN_POSTGRES_E2E=1 "
        "to run real PostgreSQL cash GL chronology"
    ),
)
@pytest.mark.asyncio
async def test_cash_payment_gl_postgresql_chronology():
    marker = "C2B3PG1"

    async with AsyncSessionLocal() as db:
        assert db.bind.dialect.name == "postgresql"

        outer = await db.begin()

        try:
            baseline_payments = await _count(
                db,
                Payment,
            )
            baseline_journals = await _count(
                db,
                JournalEntry,
            )

            # -------------------------------------------------
            # Company / user / counterparty
            # -------------------------------------------------
            company = Company(
                name=f"{marker} Company",
            )
            db.add(company)
            await db.flush()

            # -------------------------------------------------
            # Production posting requires an OPEN + unlocked
            # accounting period covering both confirmation
            # and reversal dates.
            #
            # Fixture is transaction-local and disappears
            # with final outer rollback.
            # -------------------------------------------------
            period_id = (
                await db.execute(
                    text(
                        """
                        INSERT INTO accounting_periods (
                            company_id,
                            year,
                            month,
                            start_date,
                            end_date,
                            status,
                            is_locked,
                            created_at,
                            closed_at
                        )
                        VALUES (
                            :company_id,
                            2026,
                            9,
                            DATE '2026-09-01',
                            DATE '2026-09-30',
                            'open',
                            false,
                            CURRENT_TIMESTAMP,
                            NULL
                        )
                        RETURNING id
                        """
                    ),
                    {
                        "company_id": company.id,
                    },
                )
            ).scalar_one()

            assert period_id > 0

            user = User(
                email=f"{marker.lower()}@example.invalid",
                password_hash="not-used",
                first_name="C2",
                last_name="B3",
                is_active=True,
            )
            db.add(user)
            await db.flush()

            counterparty = Counterparty(
                company_id=company.id,
                name=f"{marker} Counterparty",
                counterparty_type=CounterpartyType.BOTH,
                default_currency_code="UAH",
                is_active=True,
            )
            db.add(counterparty)
            await db.flush()

            # -------------------------------------------------
            # Production role-resolver accounts.
            #
            # general_291:
            # 681 = CUSTOMER_ADVANCES
            # 371 = SUPPLIER_ADVANCES
            #
            # Cash A/B:
            # 301 / 302 are concrete CashDesk accounts.
            # -------------------------------------------------
            customer_advances = Account(
                company_id=company.id,
                code="681",
                name=f"{marker} Customer advances",
                account_type=AccountType.LIABILITY,
                normal_balance=AccountNormalBalance.CREDIT,
                is_postable=True,
                is_system=True,
                is_active=True,
            )

            supplier_advances = Account(
                company_id=company.id,
                code="371",
                name=f"{marker} Supplier advances",
                account_type=AccountType.ASSET,
                normal_balance=AccountNormalBalance.DEBIT,
                is_postable=True,
                is_system=True,
                is_active=True,
            )

            cash_a = Account(
                company_id=company.id,
                code="301",
                name=f"{marker} Cash A",
                account_type=AccountType.ASSET,
                normal_balance=AccountNormalBalance.DEBIT,
                is_postable=True,
                is_system=False,
                is_active=True,
            )

            cash_b = Account(
                company_id=company.id,
                code="302",
                name=f"{marker} Cash B",
                account_type=AccountType.ASSET,
                normal_balance=AccountNormalBalance.DEBIT,
                is_postable=True,
                is_system=False,
                is_active=True,
            )

            db.add_all(
                [
                    customer_advances,
                    supplier_advances,
                    cash_a,
                    cash_b,
                ]
            )
            await db.flush()

            cash_desk = CashDesk(
                company_id=company.id,
                name=f"{marker} Main cash",
                code=marker,
                currency_code="UAH",
                accounting_account_id=cash_a.id,
                is_active=True,
            )
            db.add(cash_desk)
            await db.flush()

            # =================================================
            # 1. INCOMING CASH PAYMENT
            #
            # Dr Cash A
            # Cr 681 CUSTOMER_ADVANCES
            # =================================================
            incoming = await create_payment_draft(
                db,
                company_id=company.id,
                counterparty_id=counterparty.id,
                contract_id=None,
                number=f"{marker}-IN",
                direction=PaymentDirection.INCOMING,
                payment_date=date(2026, 9, 13),
                currency_code="UAH",
                amount=Decimal("1000.00"),
                created_by=user.id,
                cash_desk_id=cash_desk.id,
            )

            await confirm_payment(
                db,
                company_id=company.id,
                payment_id=incoming.id,
                confirmed_by=user.id,
            )

            await db.refresh(incoming)

            assert incoming.status == PaymentStatus.CONFIRMED

            incoming_entries = await _payment_entries(
                db,
                company_id=company.id,
                payment_id=incoming.id,
            )

            assert len(incoming_entries) == 1

            incoming_je = incoming_entries[0]

            assert (
                incoming_je.status
                == JournalEntryStatus.POSTED
            )
            assert incoming_je.reversal_of_id is None
            assert incoming_je.payment_id == incoming.id

            cash_line = _line_by_account(
                incoming_je,
                cash_a.id,
            )
            customer_advance_line = _line_by_account(
                incoming_je,
                customer_advances.id,
            )

            assert cash_line.debit == Decimal("1000.00")
            assert cash_line.credit == Decimal("0.00")

            assert (
                customer_advance_line.debit
                == Decimal("0.00")
            )
            assert (
                customer_advance_line.credit
                == Decimal("1000.00")
            )

            # Exactly one confirmation JE.
            assert len(
                await _payment_entries(
                    db,
                    company_id=company.id,
                    payment_id=incoming.id,
                )
            ) == 1

            # =================================================
            # 2. CASH DESK REMAP A -> B AFTER CONFIRMATION
            # =================================================
            await update_cash_desk(
                db,
                company_id=company.id,
                cash_desk_id=cash_desk.id,
                accounting_account_id=cash_b.id,
            )

            await db.refresh(cash_desk)

            assert (
                cash_desk.accounting_account_id
                == cash_b.id
            )

            # =================================================
            # 3. CANCEL CONFIRMED PAYMENT
            #
            # Must reverse ORIGINAL Cash A mapping.
            # Must NOT route reversal through new Cash B.
            # =================================================
            await cancel_payment(
                db,
                company_id=company.id,
                payment_id=incoming.id,
                cancelled_by=user.id,
            )

            await db.refresh(incoming)

            assert incoming.status == PaymentStatus.CANCELLED

            incoming_entries = await _payment_entries(
                db,
                company_id=company.id,
                payment_id=incoming.id,
            )

            assert len(incoming_entries) == 2

            originals = [
                entry
                for entry in incoming_entries
                if entry.reversal_of_id is None
            ]
            reversals = [
                entry
                for entry in incoming_entries
                if entry.reversal_of_id is not None
            ]

            assert len(originals) == 1
            assert len(reversals) == 1

            original = originals[0]
            reversal = reversals[0]

            assert (
                original.status
                == JournalEntryStatus.REVERSED
            )
            assert (
                reversal.status
                == JournalEntryStatus.POSTED
            )
            assert reversal.reversal_of_id == original.id

            original_cash = _line_by_account(
                original,
                cash_a.id,
            )
            reversal_cash = _line_by_account(
                reversal,
                cash_a.id,
            )

            assert (
                original_cash.debit
                == Decimal("1000.00")
            )
            assert (
                original_cash.credit
                == Decimal("0.00")
            )

            assert (
                reversal_cash.debit
                == Decimal("0.00")
            )
            assert (
                reversal_cash.credit
                == Decimal("1000.00")
            )

            # B must not appear in original or reversal JE.
            assert all(
                line.account_id != cash_b.id
                for entry in incoming_entries
                for line in entry.lines
            )

            # =================================================
            # 4. OUTGOING CASH PAYMENT USING REMAPPED B
            #
            # Dr 371 SUPPLIER_ADVANCES
            # Cr Cash B
            # =================================================
            outgoing = await create_payment_draft(
                db,
                company_id=company.id,
                counterparty_id=counterparty.id,
                contract_id=None,
                number=f"{marker}-OUT",
                direction=PaymentDirection.OUTGOING,
                payment_date=date(2026, 9, 13),
                currency_code="UAH",
                amount=Decimal("700.00"),
                created_by=user.id,
                cash_desk_id=cash_desk.id,
            )

            await confirm_payment(
                db,
                company_id=company.id,
                payment_id=outgoing.id,
                confirmed_by=user.id,
            )

            outgoing_entries = await _payment_entries(
                db,
                company_id=company.id,
                payment_id=outgoing.id,
            )

            assert len(outgoing_entries) == 1

            outgoing_je = outgoing_entries[0]

            supplier_advance_line = _line_by_account(
                outgoing_je,
                supplier_advances.id,
            )
            cash_b_line = _line_by_account(
                outgoing_je,
                cash_b.id,
            )

            assert (
                supplier_advance_line.debit
                == Decimal("700.00")
            )
            assert (
                supplier_advance_line.credit
                == Decimal("0.00")
            )

            assert cash_b_line.debit == Decimal("0.00")
            assert cash_b_line.credit == Decimal("700.00")

            # =================================================
            # 5. INACTIVE CONCRETE CASH ACCOUNT FAILS CLOSED.
            #
            # CashDesk stays active; only mapped Account is
            # invalid, so failure must come from journal routing.
            # =================================================
            inactive_cash = Account(
                company_id=company.id,
                code="303",
                name=f"{marker} Inactive cash",
                account_type=AccountType.ASSET,
                normal_balance=AccountNormalBalance.DEBIT,
                is_postable=True,
                is_system=False,
                is_active=True,
            )
            db.add(inactive_cash)
            await db.flush()

            await update_cash_desk(
                db,
                company_id=company.id,
                cash_desk_id=cash_desk.id,
                accounting_account_id=inactive_cash.id,
            )

            inactive_payment = await create_payment_draft(
                db,
                company_id=company.id,
                counterparty_id=counterparty.id,
                contract_id=None,
                number=f"{marker}-INACTIVE",
                direction=PaymentDirection.INCOMING,
                payment_date=date(2026, 9, 13),
                currency_code="UAH",
                amount=Decimal("111.00"),
                created_by=user.id,
                cash_desk_id=cash_desk.id,
            )

            inactive_cash.is_active = False
            await db.flush()

            async with db.begin_nested() as savepoint:
                with pytest.raises(PaymentStatusError):
                    await confirm_payment(
                        db,
                        company_id=company.id,
                        payment_id=inactive_payment.id,
                        confirmed_by=user.id,
                    )

                await savepoint.rollback()

            await db.refresh(inactive_payment)

            assert (
                inactive_payment.status
                == PaymentStatus.DRAFT
            )

            assert (
                len(
                    await _payment_entries(
                        db,
                        company_id=company.id,
                        payment_id=inactive_payment.id,
                    )
                )
                == 0
            )

            # =================================================
            # 6. NON-POSTABLE CASH ACCOUNT FAILS CLOSED.
            # =================================================
            nonpostable_cash = Account(
                company_id=company.id,
                code="304",
                name=f"{marker} Non-postable cash",
                account_type=AccountType.ASSET,
                normal_balance=AccountNormalBalance.DEBIT,
                is_postable=True,
                is_system=False,
                is_active=True,
            )
            db.add(nonpostable_cash)
            await db.flush()

            # Temporarily re-enable the prior account so the
            # sanctioned CashDesk update service can remap.
            inactive_cash.is_active = True
            await db.flush()

            await update_cash_desk(
                db,
                company_id=company.id,
                cash_desk_id=cash_desk.id,
                accounting_account_id=nonpostable_cash.id,
            )

            nonpostable_payment = await create_payment_draft(
                db,
                company_id=company.id,
                counterparty_id=counterparty.id,
                contract_id=None,
                number=f"{marker}-NONPOST",
                direction=PaymentDirection.OUTGOING,
                payment_date=date(2026, 9, 13),
                currency_code="UAH",
                amount=Decimal("222.00"),
                created_by=user.id,
                cash_desk_id=cash_desk.id,
            )

            nonpostable_cash.is_postable = False
            await db.flush()

            async with db.begin_nested() as savepoint:
                with pytest.raises(PaymentStatusError):
                    await confirm_payment(
                        db,
                        company_id=company.id,
                        payment_id=nonpostable_payment.id,
                        confirmed_by=user.id,
                    )

                await savepoint.rollback()

            await db.refresh(nonpostable_payment)

            assert (
                nonpostable_payment.status
                == PaymentStatus.DRAFT
            )

            assert (
                len(
                    await _payment_entries(
                        db,
                        company_id=company.id,
                        payment_id=nonpostable_payment.id,
                    )
                )
                == 0
            )

            # =================================================
            # 7. CHRONOLOGY COUNTS INSIDE TRANSACTION
            # =================================================
            payment_count = await _count(
                db,
                Payment,
            )
            journal_count = await _count(
                db,
                JournalEntry,
            )

            # 4 Payments created in test:
            # incoming cancelled, outgoing confirmed,
            # inactive draft, nonpostable draft.
            assert payment_count == baseline_payments + 4

            # incoming original + reversal + outgoing original
            assert journal_count == baseline_journals + 3

            print(
                "\nC2-B3 REAL PG GL INSIDE TX:"
                f" payments={payment_count}"
                f" journals={journal_count}"
                " incoming=DrCashA/Cr681"
                " outgoing=Dr371/CrCashB"
                " remap_reversal=CashA"
            )

        finally:
            await outer.rollback()

    # =====================================================
    # 8. INDEPENDENT ZERO-RESIDUAL PROOF
    # =====================================================
    async with AsyncSessionLocal() as verify:
        assert verify.bind.dialect.name == "postgresql"

        residual_payments = await _count(
            verify,
            Payment,
            Payment.number.like(
                f"{marker}%"
            ),
        )

        residual_desks = await _count(
            verify,
            CashDesk,
            CashDesk.code == marker,
        )

        residual_companies = await _count(
            verify,
            Company,
            Company.name == f"{marker} Company",
        )

        residual_journals = await _count(
            verify,
            JournalEntry,
            JournalEntry.description.like(
                f"Payment {marker}%"
            ),
        )

        print(
            "\nC2-B3 REAL PG GL ROLLBACK:"
            f" payments={residual_payments}"
            f" desks={residual_desks}"
            f" companies={residual_companies}"
            f" journals={residual_journals}"
        )

        assert residual_payments == 0
        assert residual_desks == 0
        assert residual_companies == 0
        assert residual_journals == 0
