from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import DocumentType
from app.models.warehouse_transfer_event import (
    WarehouseTransferEvent,
)
from app.models.warehouse_transfer_line import (
    WarehouseTransferLine,
)
from app.services.warehouse_transfer_history_service import (
    normalize_history_key,
)


class WarehouseTransferPersistenceError(ValueError):
    pass


def _positive_id(
    value: int,
    *,
    field: str,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise WarehouseTransferPersistenceError(
            f"{field} must be an integer"
        )

    if value <= 0:
        raise WarehouseTransferPersistenceError(
            f"{field} must be greater than zero"
        )

    return value


def _positive_quantity(
    value: Decimal,
) -> Decimal:
    try:
        result = Decimal(value)
    except Exception as exc:
        raise WarehouseTransferPersistenceError(
            "quantity must be a valid decimal"
        ) from exc

    if not result.is_finite() or result <= 0:
        raise WarehouseTransferPersistenceError(
            "quantity must be greater than zero"
        )

    return result


async def lock_warehouse_transfer_history(
    db: AsyncSession,
    *,
    company_id: int,
    history_key: str,
) -> tuple[WarehouseTransferEvent, ...]:
    """
    Lock exactly one immutable correction chain.

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
        select(WarehouseTransferEvent)
        .where(
            WarehouseTransferEvent.company_id
            == company_id,
            WarehouseTransferEvent.history_key
            == history_key,
        )
        .order_by(
            WarehouseTransferEvent.id
        )
        .with_for_update()
    )

    result = await db.execute(statement)

    return tuple(
        result.scalars().all()
    )


async def load_warehouse_transfer_lines(
    db: AsyncSession,
    *,
    transfer_event_ids: tuple[int, ...],
) -> tuple[WarehouseTransferLine, ...]:
    """
    Load immutable paired physical provenance for event ids.

    Locking is owned by lock_warehouse_transfer_history().
    """

    if not transfer_event_ids:
        return ()

    normalized_ids = tuple(
        _positive_id(
            value,
            field="transfer_event_id",
        )
        for value in transfer_event_ids
    )

    statement = (
        select(WarehouseTransferLine)
        .where(
            WarehouseTransferLine.transfer_event_id.in_(
                normalized_ids
            )
        )
        .order_by(
            WarehouseTransferLine.transfer_event_id,
            WarehouseTransferLine.product_id,
            WarehouseTransferLine.id,
        )
    )

    result = await db.execute(statement)

    return tuple(
        result.scalars().all()
    )


async def append_warehouse_transfer_event(
    db: AsyncSession,
    *,
    company_id: int,
    history_key: str,
    source_warehouse_id: int,
    destination_warehouse_id: int,
    transfer_date: date,
    created_by: int,
    reversal_of_id: int | None = None,
) -> WarehouseTransferEvent:
    """
    Append one immutable transfer history row.

    Does not COMMIT.
    """

    company_id = _positive_id(
        company_id,
        field="company_id",
    )
    source_warehouse_id = _positive_id(
        source_warehouse_id,
        field="source_warehouse_id",
    )
    destination_warehouse_id = _positive_id(
        destination_warehouse_id,
        field="destination_warehouse_id",
    )
    created_by = _positive_id(
        created_by,
        field="created_by",
    )

    history_key = normalize_history_key(
        history_key
    )

    if (
        source_warehouse_id
        == destination_warehouse_id
    ):
        raise WarehouseTransferPersistenceError(
            "source and destination warehouses must differ"
        )

    if not isinstance(transfer_date, date):
        raise WarehouseTransferPersistenceError(
            "transfer_date must be a date"
        )

    if reversal_of_id is not None:
        reversal_of_id = _positive_id(
            reversal_of_id,
            field="reversal_of_id",
        )

    event = WarehouseTransferEvent(
        company_id=company_id,
        history_key=history_key,
        source_warehouse_id=source_warehouse_id,
        destination_warehouse_id=(
            destination_warehouse_id
        ),
        transfer_date=transfer_date,
        created_by=created_by,
        reversal_of_id=reversal_of_id,
    )

    db.add(event)
    await db.flush()

    return event


async def append_warehouse_transfer_line(
    db: AsyncSession,
    *,
    company_id: int,
    transfer_event_id: int,
    product_id: int,
    source_warehouse_id: int,
    destination_warehouse_id: int,
    quantity: Decimal,
    issue_document_id: int,
    issue_document_line_id: int,
    receipt_document_id: int,
) -> WarehouseTransferLine:
    """
    Append immutable ISSUE -> RECEIPT provenance.

    The database composite FKs enforce:
      company
      transfer header warehouses
      product
      physical source line
      physical destination line
      document types
    """

    ids = {
        "company_id": company_id,
        "transfer_event_id": transfer_event_id,
        "product_id": product_id,
        "source_warehouse_id": source_warehouse_id,
        "destination_warehouse_id": (
            destination_warehouse_id
        ),
        "issue_document_id": issue_document_id,
        "issue_document_line_id": issue_document_line_id,
        "receipt_document_id": receipt_document_id,
    }

    normalized = {
        field: _positive_id(
            value,
            field=field,
        )
        for field, value in ids.items()
    }

    if (
        normalized["source_warehouse_id"]
        == normalized["destination_warehouse_id"]
    ):
        raise WarehouseTransferPersistenceError(
            "source and destination warehouses must differ"
        )

    quantity = _positive_quantity(
        quantity
    )

    line = WarehouseTransferLine(
        company_id=normalized["company_id"],
        transfer_event_id=normalized[
            "transfer_event_id"
        ],
        product_id=normalized["product_id"],
        source_warehouse_id=normalized[
            "source_warehouse_id"
        ],
        destination_warehouse_id=normalized[
            "destination_warehouse_id"
        ],
        quantity=quantity,
        issue_document_id=normalized[
            "issue_document_id"
        ],
        issue_document_line_id=normalized[
            "issue_document_line_id"
        ],
        issue_document_type=DocumentType.ISSUE,
        receipt_document_id=normalized[
            "receipt_document_id"
        ],
        receipt_document_type=DocumentType.RECEIPT,
    )

    db.add(line)
    await db.flush()

    return line


async def append_warehouse_transfer_valuation_layer(
    db: AsyncSession,
    *,
    company_id: int,
    transfer_line_id: int,
    product_id: int,
    destination_warehouse_id: int,
    valuation_method,
    quantity: Decimal,
    unit_cost: Decimal,
    valuation_amount: Decimal,
    source_inventory_cost_entry_id: int,
    source_stock_lot_consumption_id: int | None,
    destination_receipt_document_id: int,
    destination_receipt_document_line_id: int,
):
    """
    Append one immutable valuation slice.

    FIFO:
      source_stock_lot_consumption_id is required by higher-level
      lifecycle validation.

    Moving average:
      source_stock_lot_consumption_id must be None.

    No commit / rollback.
    """

    from app.models.warehouse_transfer_valuation_layer import (
        WarehouseTransferValuationLayer,
    )

    positive_ids = {
        "company_id": company_id,
        "transfer_line_id": transfer_line_id,
        "product_id": product_id,
        "destination_warehouse_id": destination_warehouse_id,
        "source_inventory_cost_entry_id": (
            source_inventory_cost_entry_id
        ),
        "destination_receipt_document_id": (
            destination_receipt_document_id
        ),
        "destination_receipt_document_line_id": (
            destination_receipt_document_line_id
        ),
    }

    normalized = {
        field: _positive_id(
            value,
            field=field,
        )
        for field, value in positive_ids.items()
    }

    if source_stock_lot_consumption_id is not None:
        source_stock_lot_consumption_id = _positive_id(
            source_stock_lot_consumption_id,
            field="source_stock_lot_consumption_id",
        )

    quantity = _positive_quantity(
        quantity
    )

    unit_cost = Decimal(
        unit_cost
    )
    valuation_amount = Decimal(
        valuation_amount
    )

    if (
        not unit_cost.is_finite()
        or unit_cost < 0
    ):
        raise WarehouseTransferPersistenceError(
            "unit_cost must be finite and nonnegative"
        )

    if (
        not valuation_amount.is_finite()
        or valuation_amount < 0
    ):
        raise WarehouseTransferPersistenceError(
            "valuation_amount must be finite and nonnegative"
        )

    layer = WarehouseTransferValuationLayer(
        company_id=normalized["company_id"],
        transfer_line_id=normalized[
            "transfer_line_id"
        ],
        product_id=normalized["product_id"],
        destination_warehouse_id=normalized[
            "destination_warehouse_id"
        ],
        valuation_method=valuation_method,
        quantity=quantity,
        unit_cost=unit_cost,
        valuation_amount=valuation_amount,
        source_inventory_cost_entry_id=normalized[
            "source_inventory_cost_entry_id"
        ],
        source_stock_lot_consumption_id=(
            source_stock_lot_consumption_id
        ),
        destination_receipt_document_id=normalized[
            "destination_receipt_document_id"
        ],
        destination_receipt_document_line_id=normalized[
            "destination_receipt_document_line_id"
        ],
    )

    db.add(layer)
    await db.flush()

    return layer
