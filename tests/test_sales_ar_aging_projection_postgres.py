from __future__ import annotations

import os

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from sqlalchemy import func, select

from app.core.database import AsyncSessionLocal
from app.models.company import Company
from app.models.counterparty import Counterparty
from app.models.counterparty_open_item import (
    CounterpartyOpenItem,
    CounterpartyOpenItemStatus,
    CounterpartyOpenItemType,
)
from app.models.payment import Payment
from app.models.payment_settlement_allocation import (
    PaymentSettlementAllocation,
    PaymentSettlementAllocationStatus,
)
from app.models.trade_document import TradeDocument
from app.services.counterparty_types import CounterpartyType
from app.services.payment_types import (
    PaymentDirection,
    PaymentStatus,
)
from app.services.sales_ar_aging_calculation_service import (
    SalesARAgingBucket,
)
from app.services.sales_ar_aging_projection_service import (
    load_sales_ar_aging_projection,
)
from app.services.trade_document_types import (
    TradeDirection,
    TradeDocumentKind,
    TradeDocumentStatus,
)


RUN_POSTGRES_E2E = (
    os.getenv("RUN_POSTGRES_E2E", "").strip() == "1"
)

MARKER = "B9ARPG1"


def utc(
    year: int,
    month: int,
    day: int,
) -> datetime:
    return datetime(
        year,
        month,
        day,
        12,
        0,
        tzinfo=timezone.utc,
    )


async def _count(
    db,
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
        "Set RUN_POSTGRES_E2E=1 to run real PostgreSQL "
        "Sales AR aging projection chronology"
    ),
)
@pytest.mark.asyncio
async def test_real_postgres_sales_ar_projection_chronology():
    async with AsyncSessionLocal() as db:
        assert db.bind.dialect.name == "postgresql"

        outer = await db.begin()

        try:
            company = Company(
                name=f"{MARKER} Company",
            )
            db.add(company)
            await db.flush()

            counterparty = Counterparty(
                company_id=company.id,
                name=f"{MARKER} Customer",
                counterparty_type=CounterpartyType.CUSTOMER,
                default_currency_code="EUR",
                is_active=True,
            )
            db.add(counterparty)
            await db.flush()

            invoice = TradeDocument(
                company_id=company.id,
                counterparty_id=counterparty.id,
                contract_id=None,
                number=f"{MARKER}-INV-1",
                direction=TradeDirection.SALE,
                kind=TradeDocumentKind.INVOICE,
                status=TradeDocumentStatus.CONFIRMED,
                document_date=date(2026, 8, 1),
                currency_code="EUR",
                payment_term_days=30,
                created_by=1,
            )
            db.add(invoice)
            await db.flush()

            open_item = CounterpartyOpenItem(
                company_id=company.id,
                trade_document_id=invoice.id,
                counterparty_id=counterparty.id,
                contract_id=None,
                item_type=CounterpartyOpenItemType.RECEIVABLE,
                status=CounterpartyOpenItemStatus.OPEN,
                document_date=date(2026, 8, 1),
                due_date=date(2026, 8, 31),
                currency_code="EUR",
                original_amount=Decimal("100.00"),
            )
            db.add(open_item)
            await db.flush()

            payment_historical = Payment(
                company_id=company.id,
                counterparty_id=counterparty.id,
                contract_id=None,
                number=f"{MARKER}-PAY-HIST",
                direction=PaymentDirection.INCOMING,
                status=PaymentStatus.CONFIRMED,
                payment_date=date(2026, 8, 10),
                currency_code="EUR",
                amount=Decimal("40.00"),
                created_by=1,
                confirmed_at=utc(2026, 8, 10),
            )

            payment_future = Payment(
                company_id=company.id,
                counterparty_id=counterparty.id,
                contract_id=None,
                number=f"{MARKER}-PAY-FUTURE",
                direction=PaymentDirection.INCOMING,
                status=PaymentStatus.CONFIRMED,
                payment_date=date(2026, 9, 20),
                currency_code="EUR",
                amount=Decimal("20.00"),
                created_by=1,
                confirmed_at=utc(2026, 9, 20),
            )

            payment_reversed = Payment(
                company_id=company.id,
                counterparty_id=counterparty.id,
                contract_id=None,
                number=f"{MARKER}-PAY-REVERSED",
                direction=PaymentDirection.INCOMING,
                status=PaymentStatus.CONFIRMED,
                payment_date=date(2026, 8, 5),
                currency_code="EUR",
                amount=Decimal("10.00"),
                created_by=1,
                confirmed_at=utc(2026, 8, 5),
            )

            db.add_all(
                [
                    payment_historical,
                    payment_future,
                    payment_reversed,
                ]
            )
            await db.flush()

            historical_active = PaymentSettlementAllocation(
                company_id=company.id,
                payment_id=payment_historical.id,
                open_item_id=open_item.id,
                amount=Decimal("40.00"),
                status=PaymentSettlementAllocationStatus.REVERSED,
                created_by=1,
                created_at=utc(2026, 8, 10),
                reversed_by=1,
                reversed_at=utc(2026, 9, 20),
            )

            future_allocation = PaymentSettlementAllocation(
                company_id=company.id,
                payment_id=payment_future.id,
                open_item_id=open_item.id,
                amount=Decimal("20.00"),
                status=PaymentSettlementAllocationStatus.ACTIVE,
                created_by=1,
                created_at=utc(2026, 9, 20),
                reversed_by=None,
                reversed_at=None,
            )

            reversed_before_cutoff = PaymentSettlementAllocation(
                company_id=company.id,
                payment_id=payment_reversed.id,
                open_item_id=open_item.id,
                amount=Decimal("10.00"),
                status=PaymentSettlementAllocationStatus.REVERSED,
                created_by=1,
                created_at=utc(2026, 8, 5),
                reversed_by=1,
                reversed_at=utc(2026, 9, 10),
            )

            db.add_all(
                [
                    historical_active,
                    future_allocation,
                    reversed_before_cutoff,
                ]
            )
            await db.flush()

            result = await load_sales_ar_aging_projection(
                db,
                company_id=company.id,
                counterparty_id=counterparty.id,
                as_of_date=date(2026, 9, 15),
            )

            assert len(result) == 1

            row = result[0]

            assert row.open_item_id == open_item.id
            assert row.counterparty_id == counterparty.id
            assert row.trade_document_id == invoice.id
            assert row.currency_code == "EUR"

            assert (
                row.projection.original_amount
                == Decimal("100.00")
            )
            assert (
                row.projection.settled_amount
                == Decimal("40.00")
            )
            assert (
                row.projection.open_amount
                == Decimal("60.00")
            )
            assert row.projection.days_overdue == 15
            assert (
                row.projection.bucket
                == SalesARAgingBucket.DAYS_1_30
            )

            # Cancellation after cutoff must not rewrite history.
            invoice.cancelled_at = utc(2026, 9, 20)
            await db.flush()

            before_cancel = (
                await load_sales_ar_aging_projection(
                    db,
                    company_id=company.id,
                    counterparty_id=counterparty.id,
                    as_of_date=date(2026, 9, 15),
                )
            )

            assert len(before_cancel) == 1
            assert (
                before_cancel[0].projection.open_amount
                == Decimal("60.00")
            )

            # On cancellation date the receivable leaves closing AR.
            on_cancel = (
                await load_sales_ar_aging_projection(
                    db,
                    company_id=company.id,
                    counterparty_id=counterparty.id,
                    as_of_date=date(2026, 9, 20),
                )
            )

            assert on_cancel == ()

            print(
                "\nB9 REAL PG INSIDE TX:"
                f" company={company.id}"
                f" counterparty={counterparty.id}"
                f" invoice={invoice.id}"
                f" open_item={open_item.id}"
                " as_of_2026-09-15=60.00"
                " cancellation_2026-09-20=excluded"
            )

        finally:
            await outer.rollback()

    # Independent session proves fixture left no residue.
    async with AsyncSessionLocal() as verify:
        assert verify.bind.dialect.name == "postgresql"

        companies = await _count(
            verify,
            Company,
            Company.name == f"{MARKER} Company",
        )
        counterparties = await _count(
            verify,
            Counterparty,
            Counterparty.name == f"{MARKER} Customer",
        )
        documents = await _count(
            verify,
            TradeDocument,
            TradeDocument.number == f"{MARKER}-INV-1",
        )
        payments = await _count(
            verify,
            Payment,
            Payment.number.like(f"{MARKER}-PAY-%"),
        )

        values = {
            "companies": companies,
            "counterparties": counterparties,
            "documents": documents,
            "payments": payments,
        }

        print(
            "\nB9 REAL PG ROLLBACK:",
            values,
        )

        assert values == {
            "companies": 0,
            "counterparties": 0,
            "documents": 0,
            "payments": 0,
        }
