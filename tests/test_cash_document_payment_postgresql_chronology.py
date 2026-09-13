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
from app.models.cash_document import CashDocument
from app.models.company import Company
from app.models.counterparty import Counterparty
from app.models.idempotency_record import IdempotencyRecord
from app.models.idempotency_result import IdempotencyResult
from app.models.journal_entry import (
    JournalEntry,
    JournalEntryStatus,
)
from app.models.payment import Payment
from app.models.user import User
from app.services.cash_document_types import (
    CashDocumentAlreadyReversedError,
)
from app.services.account_types import (
    AccountNormalBalance,
    AccountType,
)
from app.services.cash_document_payment_orchestration_service import (
    CASH_DOCUMENT_PAYMENT_CREATE_OPERATION,
    CashDocumentPaymentIdempotencyConflictError,
    cancel_and_reverse_cash_payment,
    create_cash_payment_reentry,
    create_confirm_and_record_cash_payment,
)
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
                    selectinload(JournalEntry.lines)
                )
                .where(
                    JournalEntry.company_id == company_id,
                    JournalEntry.payment_id == payment_id,
                )
                .order_by(JournalEntry.id)
            )
        )
        .scalars()
        .all()
    )


@pytest.mark.skipif(
    not RUN_POSTGRES_E2E,
    reason=(
        "Set RUN_POSTGRES_E2E=1 to run real PostgreSQL "
        "CashDocument payment orchestration chronology"
    ),
)
@pytest.mark.asyncio
async def test_cash_document_payment_postgresql_chronology():
    marker = "C2B4PG1"
    idem_key = f"{marker}-CREATE"

    async with AsyncSessionLocal() as db:
        assert db.bind.dialect.name == "postgresql"

        outer = await db.begin()

        try:
            baseline_payments = await _count(
                db,
                Payment,
            )
            baseline_documents = await _count(
                db,
                CashDocument,
            )
            baseline_journals = await _count(
                db,
                JournalEntry,
            )
            baseline_idem_records = await _count(
                db,
                IdempotencyRecord,
            )
            baseline_idem_results = await _count(
                db,
                IdempotencyResult,
            )

            # =============================================
            # FOUNDATION
            # =============================================
            company = Company(
                name=f"{marker} Company",
            )
            db.add(company)
            await db.flush()

            # Payment posting + cancellation reversal must
            # both fall inside an OPEN accounting period.
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
                last_name="B4",
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

            # Semantic resolver roles.
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

            cash_account = Account(
                company_id=company.id,
                code="301",
                name=f"{marker} Cash",
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
                    cash_account,
                ]
            )
            await db.flush()

            cash_desk = CashDesk(
                company_id=company.id,
                name=f"{marker} Cash desk",
                code=marker,
                currency_code="UAH",
                accounting_account_id=cash_account.id,
                is_active=True,
            )
            db.add(cash_desk)
            await db.flush()

            # =============================================
            # 1. CREATE
            #
            # one Payment
            # one original CashDocument
            # one confirmation JE
            # one idempotency record + reusable result
            # =============================================
            created = (
                await create_confirm_and_record_cash_payment(
                    db,
                    company_id=company.id,
                    idempotency_key=idem_key,
                    cash_desk_id=cash_desk.id,
                    payment_number=f"{marker}-PAY-1",
                    document_number=f"{marker}-DOC-1",
                    direction=PaymentDirection.INCOMING,
                    document_date=date(2026, 9, 13),
                    amount=Decimal("1000.00"),
                    currency_code="UAH",
                    counterparty_id=counterparty.id,
                    contract_id=None,
                    created_by=user.id,
                    external_reference=f"{marker}-REF",
                    description="initial cash receipt",
                )
            )

            payment_1 = created.payment
            document_1 = created.cash_document

            assert payment_1.status == PaymentStatus.CONFIRMED
            assert payment_1.cash_desk_id == cash_desk.id
            assert payment_1.bank_account_id is None

            assert document_1.payment_id == payment_1.id
            assert document_1.cash_desk_id == cash_desk.id
            assert document_1.reversal_of_id is None

            payment_1_entries = await _payment_entries(
                db,
                company_id=company.id,
                payment_id=payment_1.id,
            )

            assert len(payment_1_entries) == 1
            assert (
                payment_1_entries[0].status
                == JournalEntryStatus.POSTED
            )

            assert (
                await _count(
                    db,
                    IdempotencyRecord,
                    IdempotencyRecord.company_id
                    == company.id,
                    IdempotencyRecord.operation
                    == CASH_DOCUMENT_PAYMENT_CREATE_OPERATION,
                    IdempotencyRecord.idempotency_key
                    == idem_key,
                )
                == 1
            )

            assert (
                await _count(
                    db,
                    IdempotencyResult,
                )
                == baseline_idem_results + 1
            )

            # =============================================
            # 2. SAME KEY + SAME PAYLOAD = SAME RESULT
            #
            # No second Payment.
            # No second CashDocument.
            # No second JE.
            # =============================================
            replay = (
                await create_confirm_and_record_cash_payment(
                    db,
                    company_id=company.id,
                    idempotency_key=idem_key,
                    cash_desk_id=cash_desk.id,
                    payment_number=f"{marker}-PAY-1",
                    document_number=f"{marker}-DOC-1",
                    direction=PaymentDirection.INCOMING,
                    document_date=date(2026, 9, 13),
                    amount=Decimal("1000.00"),
                    currency_code="UAH",
                    counterparty_id=counterparty.id,
                    contract_id=None,
                    created_by=user.id,
                    external_reference=f"{marker}-REF",
                    description="initial cash receipt",
                )
            )

            assert replay.payment.id == payment_1.id
            assert (
                replay.cash_document.id
                == document_1.id
            )

            assert (
                await _count(
                    db,
                    Payment,
                )
                == baseline_payments + 1
            )

            assert (
                await _count(
                    db,
                    CashDocument,
                )
                == baseline_documents + 1
            )

            assert (
                await _count(
                    db,
                    JournalEntry,
                )
                == baseline_journals + 1
            )

            # =============================================
            # 3. SAME KEY + DIFFERENT PAYLOAD = FAIL CLOSED
            #
            # The orchestration boundary must not leak
            # idempotency infrastructure exceptions.
            # Different payload under the same key is exposed
            # as an orchestration-domain conflict.
            # =============================================
            with pytest.raises(
                CashDocumentPaymentIdempotencyConflictError
            ):
                await create_confirm_and_record_cash_payment(
                    db,
                    company_id=company.id,
                    idempotency_key=idem_key,
                    cash_desk_id=cash_desk.id,
                    payment_number=f"{marker}-PAY-DIFFERENT",
                    document_number=f"{marker}-DOC-1",
                    direction=PaymentDirection.INCOMING,
                    document_date=date(2026, 9, 13),
                    amount=Decimal("1000.00"),
                    currency_code="UAH",
                    counterparty_id=counterparty.id,
                    contract_id=None,
                    created_by=user.id,
                    external_reference=f"{marker}-REF",
                    description="initial cash receipt",
                )

            assert (
                await _count(db, Payment)
                == baseline_payments + 1
            )
            assert (
                await _count(db, CashDocument)
                == baseline_documents + 1
            )
            assert (
                await _count(db, JournalEntry)
                == baseline_journals + 1
            )

            # =============================================
            # 4. REVERSE
            #
            # Existing Payment becomes CANCELLED.
            # Existing confirmation JE gets reversed.
            # One reversal CashDocument is appended.
            # No second Payment.
            # =============================================
            reversed_result = (
                await cancel_and_reverse_cash_payment(
                    db,
                    company_id=company.id,
                    cash_document_id=document_1.id,
                    reversal_document_number=(
                        f"{marker}-DOC-1-R"
                    ),
                    created_by=user.id,
                )
            )

            assert (
                reversed_result.payment.id
                == payment_1.id
            )
            assert (
                reversed_result.payment.status
                == PaymentStatus.CANCELLED
            )

            reversal_document = (
                reversed_result.cash_document
            )

            assert (
                reversal_document.reversal_of_id
                == document_1.id
            )
            assert (
                reversal_document.payment_id
                == payment_1.id
            )

            assert (
                await _count(db, Payment)
                == baseline_payments + 1
            )

            assert (
                await _count(db, CashDocument)
                == baseline_documents + 2
            )

            payment_1_entries = await _payment_entries(
                db,
                company_id=company.id,
                payment_id=payment_1.id,
            )

            assert len(payment_1_entries) == 2

            originals = [
                entry
                for entry in payment_1_entries
                if entry.reversal_of_id is None
            ]
            reversals = [
                entry
                for entry in payment_1_entries
                if entry.reversal_of_id is not None
            ]

            assert len(originals) == 1
            assert len(reversals) == 1

            assert (
                originals[0].status
                == JournalEntryStatus.REVERSED
            )
            assert (
                reversals[0].status
                == JournalEntryStatus.POSTED
            )
            assert (
                reversals[0].reversal_of_id
                == originals[0].id
            )

            # =============================================
            # 5. SECOND REVERSAL FAILS CLOSED
            # =============================================
            with pytest.raises(
                CashDocumentAlreadyReversedError
            ):
                await cancel_and_reverse_cash_payment(
                    db,
                    company_id=company.id,
                    cash_document_id=document_1.id,
                    reversal_document_number=(
                        f"{marker}-DOC-1-R2"
                    ),
                    created_by=user.id,
                )

            assert (
                await _count(db, Payment)
                == baseline_payments + 1
            )
            assert (
                await _count(db, CashDocument)
                == baseline_documents + 2
            )
            assert (
                await _count(db, JournalEntry)
                == baseline_journals + 2
            )

            # =============================================
            # 6. RE-ENTRY
            #
            # Must create:
            #   new Payment
            #   new confirmation JE
            #   new original CashDocument
            # and preserve prior immutable history.
            # =============================================
            reentry = await create_cash_payment_reentry(
                db,
                company_id=company.id,
                reversed_cash_document_id=document_1.id,
                cash_desk_id=cash_desk.id,
                payment_number=f"{marker}-PAY-2",
                document_number=f"{marker}-DOC-2",
                direction=PaymentDirection.OUTGOING,
                document_date=date(2026, 9, 13),
                amount=Decimal("400.00"),
                currency_code="UAH",
                counterparty_id=counterparty.id,
                contract_id=None,
                created_by=user.id,
                external_reference=(
                    f"{marker}-REF-2"
                ),
                description="replacement cash payment",
            )

            payment_2 = reentry.payment
            document_2 = reentry.cash_document

            assert payment_2.id != payment_1.id
            assert payment_2.status == PaymentStatus.CONFIRMED

            assert document_2.id != document_1.id
            assert document_2.id != reversal_document.id
            assert document_2.payment_id == payment_2.id
            assert document_2.reversal_of_id is None

            assert (
                await _count(db, Payment)
                == baseline_payments + 2
            )

            assert (
                await _count(db, CashDocument)
                == baseline_documents + 3
            )

            payment_2_entries = await _payment_entries(
                db,
                company_id=company.id,
                payment_id=payment_2.id,
            )

            assert len(payment_2_entries) == 1
            assert (
                payment_2_entries[0].status
                == JournalEntryStatus.POSTED
            )

            # Total JEs:
            # payment1 original + reversal
            # payment2 original
            assert (
                await _count(db, JournalEntry)
                == baseline_journals + 3
            )

            # Idempotency belongs only to create operation #1.
            assert (
                await _count(db, IdempotencyRecord)
                == baseline_idem_records + 1
            )
            assert (
                await _count(db, IdempotencyResult)
                == baseline_idem_results + 1
            )

            # =============================================
            # 7. IMMUTABLE HISTORY
            # =============================================
            original_again = (
                await db.execute(
                    select(CashDocument).where(
                        CashDocument.id
                        == document_1.id
                    )
                )
            ).scalar_one()

            assert (
                original_again.payment_id
                == payment_1.id
            )
            assert (
                original_again.reversal_of_id
                is None
            )
            assert (
                original_again.document_number
                == f"{marker}-DOC-1"
            )
            assert (
                Decimal(original_again.amount)
                == Decimal("1000.00")
            )

            print(
                "\nC2-B4 REAL PG INSIDE TX:"
                f" payment1={payment_1.id}"
                f" document1={document_1.id}"
                f" reversal={reversal_document.id}"
                f" payment2={payment_2.id}"
                f" document2={document_2.id}"
                " replay=same-result"
                " conflicting-replay=fail-closed"
                " payment1-je=original+reversal"
                " payment2-je=one-original"
            )

        finally:
            await outer.rollback()

    # =============================================
    # 8. INDEPENDENT ZERO RESIDUAL
    # =============================================
    async with AsyncSessionLocal() as verify:
        assert verify.bind.dialect.name == "postgresql"

        residual_payments = await _count(
            verify,
            Payment,
            Payment.number.like(
                f"{marker}%"
            ),
        )

        residual_documents = await _count(
            verify,
            CashDocument,
            CashDocument.document_number.like(
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

        residual_idem_records = await _count(
            verify,
            IdempotencyRecord,
            IdempotencyRecord.operation
            == CASH_DOCUMENT_PAYMENT_CREATE_OPERATION,
            IdempotencyRecord.idempotency_key
            == idem_key,
        )

        # Result rows reference idempotency records with
        # ON DELETE CASCADE, so outer rollback must leave zero.
        residual_idem_results = int(
            (
                await verify.execute(
                    text(
                        """
                        SELECT count(*)
                        FROM idempotency_results ir
                        JOIN idempotency_records rec
                          ON rec.id = ir.idempotency_record_id
                        WHERE rec.operation = :operation
                          AND rec.idempotency_key = :key
                        """
                    ),
                    {
                        "operation":
                            CASH_DOCUMENT_PAYMENT_CREATE_OPERATION,
                        "key": idem_key,
                    },
                )
            ).scalar_one()
        )

        residual_periods = int(
            (
                await verify.execute(
                    text(
                        """
                        SELECT count(*)
                        FROM accounting_periods ap
                        JOIN companies c
                          ON c.id = ap.company_id
                        WHERE c.name = :company_name
                        """
                    ),
                    {
                        "company_name":
                            f"{marker} Company",
                    },
                )
            ).scalar_one()
        )

        values = {
            "payments": residual_payments,
            "documents": residual_documents,
            "desks": residual_desks,
            "companies": residual_companies,
            "idem_records": residual_idem_records,
            "idem_results": residual_idem_results,
            "periods": residual_periods,
        }

        print(
            "\nC2-B4 REAL PG ROLLBACK:",
            values,
        )

        assert values == {
            "payments": 0,
            "documents": 0,
            "desks": 0,
            "companies": 0,
            "idem_records": 0,
            "idem_results": 0,
            "periods": 0,
        }
