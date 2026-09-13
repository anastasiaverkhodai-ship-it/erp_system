import os
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal
from app.models.account import Account
from app.models.cash_desk import CashDesk
from app.models.cash_document import CashDocument
from app.models.company import Company
from app.models.counterparty import Counterparty
from app.models.journal_entry import JournalEntry
from app.models.payment import Payment
from app.models.user import User
from app.services.account_types import (
    AccountNormalBalance,
    AccountType,
)
from app.services.cash_document_service import (
    CashDocumentAlreadyReversedError,
    CashDocumentValidationError,
    create_cash_document_evidence,
    reverse_cash_document_evidence,
)
from app.services.cash_document_types import CashDocumentFacts
from app.services.counterparty_types import CounterpartyType
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
    stmt = select(func.count()).select_from(model)

    if criteria:
        stmt = stmt.where(*criteria)

    return int(
        (await db.execute(stmt)).scalar_one()
    )


@pytest.mark.skipif(
    not RUN_POSTGRES_E2E,
    reason=(
        "Set RUN_POSTGRES_E2E=1 "
        "to run the real PostgreSQL chronology test"
    ),
)
@pytest.mark.asyncio
async def test_cash_document_postgresql_chronology():
    marker = "C2B2PG1"

    async with AsyncSessionLocal() as db:
        assert db.bind.dialect.name == "postgresql"
        outer = await db.begin()

        try:
            # ---------------------------------------------
            # Baseline counts for rollback proof.
            # ---------------------------------------------
            baseline_documents = await _count(
                db,
                CashDocument,
            )
            baseline_payments = await _count(
                db,
                Payment,
            )
            baseline_journals = await _count(
                db,
                JournalEntry,
            )

            # ---------------------------------------------
            # Minimal isolated master data.
            # ---------------------------------------------
            company = Company(
                name=f"{marker} Company",
            )
            db.add(company)
            await db.flush()

            user = User(
                email=f"{marker.lower()}@example.invalid",
                password_hash="not-used",
                first_name="C2",
                last_name="B2",
                is_active=True,
            )
            db.add(user)
            await db.flush()

            account = Account(
                company_id=company.id,
                code=f"{marker}-301",
                name="Cash chronology account",
                account_type=AccountType.ASSET,
                normal_balance=AccountNormalBalance.DEBIT,
                is_postable=True,
                is_system=False,
                is_active=True,
            )
            db.add(account)
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

            cash_desk = CashDesk(
                company_id=company.id,
                name="Main chronology cash desk",
                code=marker,
                currency_code="UAH",
                accounting_account_id=account.id,
                is_active=True,
            )
            db.add(cash_desk)
            await db.flush()

            # ---------------------------------------------
            # Existing cash Payment.
            #
            # B2 intentionally does not create/confirm it.
            # Keep it DRAFT: B2 persistence only proves
            # operational evidence identity.
            # ---------------------------------------------
            payment = Payment(
                company_id=company.id,
                bank_account_id=None,
                cash_desk_id=cash_desk.id,
                counterparty_id=counterparty.id,
                contract_id=None,
                number=f"{marker}-PAY-001",
                direction=PaymentDirection.INCOMING,
                status=PaymentStatus.DRAFT,
                payment_date=date(2026, 9, 13),
                currency_code="UAH",
                amount=Decimal("1250.00"),
                external_reference=f"{marker}-EXT",
                description="B2 PostgreSQL chronology",
                created_by=user.id,
            )
            db.add(payment)
            await db.flush()

            payment_id = payment.id
            cash_desk_id = cash_desk.id

            # ---------------------------------------------
            # CREATE ORIGINAL IMMUTABLE EVIDENCE.
            # ---------------------------------------------
            facts = CashDocumentFacts(
                document_number=f"{marker}-CD-001",
                direction=PaymentDirection.INCOMING,
                document_date=date(2026, 9, 13),
                amount=Decimal("1250.00"),
                currency_code="uah",
                counterparty_id=counterparty.id,
                contract_id=None,
                external_reference=f" {marker}-DOC ",
                description=" original evidence ",
            )

            original = await create_cash_document_evidence(
                db,
                company_id=company.id,
                cash_desk_id=cash_desk.id,
                payment_id=payment.id,
                facts=facts,
                created_by=user.id,
            )

            assert original.reversal_of_id is None
            assert original.payment_id == payment_id
            assert original.cash_desk_id == cash_desk_id
            assert original.currency_code == "UAH"
            assert original.amount == Decimal("1250.00")

            original_id = original.id

            # B2 must not create another Payment or any JE.
            assert await _count(
                db,
                Payment,
            ) == baseline_payments + 1

            assert await _count(
                db,
                JournalEntry,
            ) == baseline_journals

            # ---------------------------------------------
            # REVERSAL EVENT.
            # Same Payment and CashDesk identity.
            # ---------------------------------------------
            reversal = await reverse_cash_document_evidence(
                db,
                company_id=company.id,
                cash_document_id=original.id,
                reversal_document_number=(
                    f"{marker}-CD-REV-001"
                ),
                created_by=user.id,
            )

            assert reversal.reversal_of_id == original_id
            assert reversal.payment_id == payment_id
            assert reversal.cash_desk_id == cash_desk_id
            assert reversal.direction == original.direction
            assert reversal.amount == original.amount
            assert (
                reversal.currency_code
                == original.currency_code
            )

            # Original is unchanged.
            await db.refresh(original)

            assert original.id == original_id
            assert original.reversal_of_id is None
            assert original.payment_id == payment_id
            assert original.cash_desk_id == cash_desk_id
            assert original.amount == Decimal("1250.00")

            # ---------------------------------------------
            # DOUBLE REVERSAL FAILS CLOSED.
            # ---------------------------------------------
            with pytest.raises(
                CashDocumentAlreadyReversedError
            ):
                await reverse_cash_document_evidence(
                    db,
                    company_id=company.id,
                    cash_document_id=original.id,
                    reversal_document_number=(
                        f"{marker}-CD-REV-002"
                    ),
                    created_by=user.id,
                )

            # Reversal cannot itself be reversed.
            with pytest.raises(
                CashDocumentValidationError
            ):
                await reverse_cash_document_evidence(
                    db,
                    company_id=company.id,
                    cash_document_id=reversal.id,
                    reversal_document_number=(
                        f"{marker}-CD-REV-OF-REV"
                    ),
                    created_by=user.id,
                )

            # ---------------------------------------------
            # RE-ENTRY = NEW ORIGINAL + NEW PAYMENT.
            # Historical original/reversal stay untouched.
            # ---------------------------------------------
            payment2 = Payment(
                company_id=company.id,
                bank_account_id=None,
                cash_desk_id=cash_desk.id,
                counterparty_id=counterparty.id,
                contract_id=None,
                number=f"{marker}-PAY-002",
                direction=PaymentDirection.INCOMING,
                status=PaymentStatus.DRAFT,
                payment_date=date(2026, 9, 13),
                currency_code="UAH",
                amount=Decimal("1250.00"),
                external_reference=f"{marker}-REENTRY",
                description="B2 re-entry",
                created_by=user.id,
            )
            db.add(payment2)
            await db.flush()

            reentry_facts = CashDocumentFacts(
                document_number=f"{marker}-CD-002",
                direction=PaymentDirection.INCOMING,
                document_date=date(2026, 9, 13),
                amount=Decimal("1250.00"),
                currency_code="UAH",
                counterparty_id=counterparty.id,
                contract_id=None,
                external_reference=None,
                description="re-entry",
            )

            reentry = await create_cash_document_evidence(
                db,
                company_id=company.id,
                cash_desk_id=cash_desk.id,
                payment_id=payment2.id,
                facts=reentry_facts,
                created_by=user.id,
            )

            assert reentry.id != original.id
            assert reentry.payment_id != original.payment_id
            assert reentry.reversal_of_id is None

            # ---------------------------------------------
            # IDENTITY MISMATCHES FAIL CLOSED.
            # Each check uses SAVEPOINT so expected DB/
            # service failure cannot poison outer tx.
            # ---------------------------------------------
            bad_cases = (
                (
                    "amount",
                    CashDocumentFacts(
                        document_number=f"{marker}-BAD-AMOUNT",
                        direction=PaymentDirection.INCOMING,
                        document_date=date(2026, 9, 13),
                        amount=Decimal("999.00"),
                        currency_code="UAH",
                        counterparty_id=counterparty.id,
                        contract_id=None,
                    ),
                ),
                (
                    "currency",
                    CashDocumentFacts(
                        document_number=f"{marker}-BAD-CUR",
                        direction=PaymentDirection.INCOMING,
                        document_date=date(2026, 9, 13),
                        amount=Decimal("1250.00"),
                        currency_code="EUR",
                        counterparty_id=counterparty.id,
                        contract_id=None,
                    ),
                ),
                (
                    "direction",
                    CashDocumentFacts(
                        document_number=f"{marker}-BAD-DIR",
                        direction=PaymentDirection.OUTGOING,
                        document_date=date(2026, 9, 13),
                        amount=Decimal("1250.00"),
                        currency_code="UAH",
                        counterparty_id=counterparty.id,
                        contract_id=None,
                    ),
                ),
                (
                    "date",
                    CashDocumentFacts(
                        document_number=f"{marker}-BAD-DATE",
                        direction=PaymentDirection.INCOMING,
                        document_date=date(2026, 9, 14),
                        amount=Decimal("1250.00"),
                        currency_code="UAH",
                        counterparty_id=counterparty.id,
                        contract_id=None,
                    ),
                ),
            )

            for _, bad_facts in bad_cases:
                async with db.begin_nested():
                    with pytest.raises(
                        CashDocumentValidationError
                    ):
                        await create_cash_document_evidence(
                            db,
                            company_id=company.id,
                            cash_desk_id=cash_desk.id,
                            payment_id=payment2.id,
                            facts=bad_facts,
                            created_by=user.id,
                        )

            # ---------------------------------------------
            # B2 side-effect boundaries.
            # ---------------------------------------------
            assert await _count(
                db,
                JournalEntry,
            ) == baseline_journals

            documents_inside = await _count(
                db,
                CashDocument,
            )

            assert documents_inside == baseline_documents + 3

            # original + reversal + re-entry
            print(
                "\nC2-B2 REAL PG INSIDE TX:"
                f" cash_documents={documents_inside}"
                f" payments={await _count(db, Payment)}"
                f" journals={await _count(db, JournalEntry)}"
            )

        finally:
            await outer.rollback()

        # -----------------------------------------------------
        # Independent session proves zero residual.
        # -----------------------------------------------------
    async with AsyncSessionLocal() as verify:
        assert verify.bind.dialect.name == "postgresql"
        residual_documents = await _count(
            verify,
            CashDocument,
            CashDocument.document_number.like(
                f"{marker}%"
            ),
        )
        residual_payments = await _count(
            verify,
            Payment,
            Payment.number.like(
                f"{marker}%"
            ),
        )
        residual_cash_desks = await _count(
            verify,
            CashDesk,
            CashDesk.code == marker,
        )
        residual_companies = await _count(
            verify,
            Company,
            Company.name == f"{marker} Company",
        )

        assert residual_documents == 0
        assert residual_payments == 0
        assert residual_cash_desks == 0
        assert residual_companies == 0

        print(
            "\nC2-B2 REAL PG ROLLBACK:"
            " documents=0"
            " payments=0"
            " cash_desks=0"
            " companies=0"
        )
