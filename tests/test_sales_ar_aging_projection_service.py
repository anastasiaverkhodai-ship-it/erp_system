from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.counterparty_open_item import (
    CounterpartyOpenItemType,
)
from app.services.sales_ar_aging_calculation_service import (
    SalesARAgingBucket,
)
from app.services.sales_ar_aging_projection_service import (
    load_sales_ar_aging_projection,
)


def dt(
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


def open_item(
    *,
    item_id: int = 10,
    amount: str = "100.00",
    document_date: date = date(2026, 8, 1),
    due_date: date = date(2026, 8, 31),
):
    return SimpleNamespace(
        id=item_id,
        company_id=1,
        counterparty_id=20,
        trade_document_id=30,
        item_type=CounterpartyOpenItemType.RECEIVABLE,
        original_amount=Decimal(amount),
        currency_code="EUR",
        document_date=document_date,
        due_date=due_date,
    )


def document(
    *,
    cancelled_at=None,
):
    return SimpleNamespace(
        id=30,
        company_id=1,
        cancelled_at=cancelled_at,
    )


def allocation(
    *,
    allocation_id: int = 100,
    amount: str = "40.00",
    created_at: datetime = dt(2026, 8, 10),
    reversed_at=None,
):
    return SimpleNamespace(
        id=allocation_id,
        company_id=1,
        open_item_id=10,
        amount=Decimal(amount),
        created_at=created_at,
        reversed_at=reversed_at,
    )


def db_with_results(
    *,
    open_item_rows,
    allocations,
):
    db = AsyncMock()

    first = MagicMock()
    first.all.return_value = open_item_rows

    scalar_result = MagicMock()
    scalar_result.all.return_value = allocations

    second = MagicMock()
    second.scalars.return_value = scalar_result

    db.execute.side_effect = [
        first,
        second,
    ]

    return db


@pytest.mark.asyncio
async def test_historical_partial_settlement_projection():
    db = db_with_results(
        open_item_rows=[
            (
                open_item(),
                document(),
            ),
        ],
        allocations=[
            allocation(),
        ],
    )

    result = await load_sales_ar_aging_projection(
        db,
        company_id=1,
        as_of_date=date(2026, 9, 15),
    )

    assert len(result) == 1

    row = result[0]

    assert row.open_item_id == 10
    assert row.currency_code == "EUR"
    assert row.projection.original_amount == Decimal("100.00")
    assert row.projection.settled_amount == Decimal("40.00")
    assert row.projection.open_amount == Decimal("60.00")
    assert row.projection.days_overdue == 15
    assert (
        row.projection.bucket
        == SalesARAgingBucket.DAYS_1_30
    )


@pytest.mark.asyncio
async def test_future_settlement_is_ignored_as_of_date():
    db = db_with_results(
        open_item_rows=[
            (
                open_item(),
                document(),
            ),
        ],
        allocations=[
            allocation(
                created_at=dt(2026, 9, 20),
            ),
        ],
    )

    result = await load_sales_ar_aging_projection(
        db,
        company_id=1,
        as_of_date=date(2026, 9, 15),
    )

    assert result[0].projection.open_amount == Decimal(
        "100.00"
    )


@pytest.mark.asyncio
async def test_reversed_after_as_of_remains_historically_settled():
    db = db_with_results(
        open_item_rows=[
            (
                open_item(),
                document(),
            ),
        ],
        allocations=[
            allocation(
                created_at=dt(2026, 8, 10),
                reversed_at=dt(2026, 9, 20),
            ),
        ],
    )

    result = await load_sales_ar_aging_projection(
        db,
        company_id=1,
        as_of_date=date(2026, 9, 15),
    )

    assert result[0].projection.open_amount == Decimal(
        "60.00"
    )


@pytest.mark.asyncio
async def test_reversed_on_as_of_is_not_settled():
    db = db_with_results(
        open_item_rows=[
            (
                open_item(),
                document(),
            ),
        ],
        allocations=[
            allocation(
                created_at=dt(2026, 8, 10),
                reversed_at=dt(2026, 9, 15),
            ),
        ],
    )

    result = await load_sales_ar_aging_projection(
        db,
        company_id=1,
        as_of_date=date(2026, 9, 15),
    )

    assert result[0].projection.open_amount == Decimal(
        "100.00"
    )


@pytest.mark.asyncio
async def test_invoice_cancelled_after_as_of_is_historically_visible():
    db = db_with_results(
        open_item_rows=[
            (
                open_item(),
                document(
                    cancelled_at=dt(2026, 9, 20),
                ),
            ),
        ],
        allocations=[],
    )

    result = await load_sales_ar_aging_projection(
        db,
        company_id=1,
        as_of_date=date(2026, 9, 15),
    )

    assert len(result) == 1
    assert result[0].projection.open_amount == Decimal(
        "100.00"
    )


@pytest.mark.asyncio
async def test_invoice_cancelled_on_as_of_is_excluded():
    db = AsyncMock()

    first = MagicMock()
    first.all.return_value = [
        (
            open_item(),
            document(
                cancelled_at=dt(2026, 9, 15),
            ),
        ),
    ]

    db.execute.return_value = first

    result = await load_sales_ar_aging_projection(
        db,
        company_id=1,
        as_of_date=date(2026, 9, 15),
    )

    assert result == ()
    assert db.execute.await_count == 1


@pytest.mark.asyncio
async def test_fully_settled_item_is_not_in_aging_population():
    db = db_with_results(
        open_item_rows=[
            (
                open_item(),
                document(),
            ),
        ],
        allocations=[
            allocation(
                amount="100.00",
            ),
        ],
    )

    result = await load_sales_ar_aging_projection(
        db,
        company_id=1,
        as_of_date=date(2026, 9, 15),
    )

    assert result == ()


@pytest.mark.asyncio
async def test_current_open_item_status_is_not_required():
    item = open_item()

    # Deliberately no .status attribute. Historical projection must
    # reconstruct balance from chronology rather than current status.
    assert not hasattr(item, "status")

    db = db_with_results(
        open_item_rows=[
            (
                item,
                document(),
            ),
        ],
        allocations=[],
    )

    result = await load_sales_ar_aging_projection(
        db,
        company_id=1,
        as_of_date=date(2026, 9, 15),
    )

    assert len(result) == 1


@pytest.mark.asyncio
async def test_counterparty_filter_is_supported():
    db = db_with_results(
        open_item_rows=[
            (
                open_item(),
                document(),
            ),
        ],
        allocations=[],
    )

    result = await load_sales_ar_aging_projection(
        db,
        company_id=1,
        counterparty_id=20,
        as_of_date=date(2026, 9, 15),
    )

    assert len(result) == 1


@pytest.mark.asyncio
async def test_no_open_items_avoids_settlement_query():
    db = AsyncMock()

    first = MagicMock()
    first.all.return_value = []

    db.execute.return_value = first

    result = await load_sales_ar_aging_projection(
        db,
        company_id=1,
        as_of_date=date(2026, 9, 15),
    )

    assert result == ()
    assert db.execute.await_count == 1
