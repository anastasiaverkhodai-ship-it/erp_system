from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.inventory_count_event import (
    InventoryCountEvent,
)
from app.services.inventory_count_history_service import (
    normalize_history_key,
)


class InventoryCountPersistenceError(ValueError):
    pass


def _positive_id(
    value: int,
    *,
    field: str,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise InventoryCountPersistenceError(
            f"{field} must be an integer"
        )

    if value <= 0:
        raise InventoryCountPersistenceError(
            f"{field} must be greater than zero"
        )

    return value


def _nonnegative_quantity(
    value: Decimal,
    *,
    field: str,
) -> Decimal:
    try:
        result = Decimal(value)
    except Exception as exc:
        raise InventoryCountPersistenceError(
            f"{field} must be a valid decimal"
        ) from exc

    if not result.is_finite() or result < 0:
        raise InventoryCountPersistenceError(
            f"{field} cannot be negative"
        )

    return result


async def lock_inventory_count_history(
    db: AsyncSession,
    *,
    company_id: int,
    history_key: str,
) -> tuple[InventoryCountEvent, ...]:
    """
    Lock exactly one inventory-count correction chain.

    Caller owns transaction boundaries.
    """

    company_id = _positive_id(
        company_id,
        field="company_id",
    )
    history_key = normalize_history_key(
        history_key
    )

    statement = (
        select(InventoryCountEvent)
        .where(
            InventoryCountEvent.company_id
            == company_id,
            InventoryCountEvent.history_key
            == history_key,
        )
        .order_by(
            InventoryCountEvent.id
        )
        .with_for_update()
    )

    result = await db.execute(statement)

    return tuple(
        result.scalars().all()
    )


async def append_inventory_count_event(
    db: AsyncSession,
    *,
    company_id: int,
    history_key: str,
    product_id: int,
    warehouse_id: int,
    count_date: date,
    expected_quantity: Decimal,
    counted_quantity: Decimal,
    created_by: int,
    reversal_of_id: int | None = None,
) -> InventoryCountEvent:
    """
    Append one immutable inventory count history row.

    Count snapshot itself does not post stock.
    Does not COMMIT.
    """

    company_id = _positive_id(
        company_id,
        field="company_id",
    )
    product_id = _positive_id(
        product_id,
        field="product_id",
    )
    warehouse_id = _positive_id(
        warehouse_id,
        field="warehouse_id",
    )
    created_by = _positive_id(
        created_by,
        field="created_by",
    )

    history_key = normalize_history_key(
        history_key
    )

    if not isinstance(count_date, date):
        raise InventoryCountPersistenceError(
            "count_date must be a date"
        )

    expected_quantity = _nonnegative_quantity(
        expected_quantity,
        field="expected_quantity",
    )
    counted_quantity = _nonnegative_quantity(
        counted_quantity,
        field="counted_quantity",
    )

    if reversal_of_id is not None:
        reversal_of_id = _positive_id(
            reversal_of_id,
            field="reversal_of_id",
        )

    event = InventoryCountEvent(
        company_id=company_id,
        history_key=history_key,
        product_id=product_id,
        warehouse_id=warehouse_id,
        count_date=count_date,
        expected_quantity=expected_quantity,
        counted_quantity=counted_quantity,
        created_by=created_by,
        reversal_of_id=reversal_of_id,
    )

    db.add(event)
    await db.flush()

    return event
