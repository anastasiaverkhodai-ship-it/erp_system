from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import (
    Document,
    DocumentType,
)
from app.models.document_line import (
    DocumentLine,
)
from app.models.stock_ledger import (
    StockLedger,
    StockMovementType,
)
from app.models.trade_return_event import (
    TradeReturnEvent,
)
from app.services.accounting_period_service import (
    ensure_period_open,
)
from app.services.warehouse_posting import (
    get_locked_stock_balance,
)


ZERO = Decimal(
    "0"
)


class SalesReturnWarehouseQuantityError(
    Exception
):
    """Base Sales Return warehouse quantity error."""


class SalesReturnWarehouseQuantitySourceError(
    SalesReturnWarehouseQuantityError
):
    """Return source/provenance is invalid."""


class SalesReturnWarehouseQuantityDuplicateError(
    SalesReturnWarehouseQuantityError
):
    """Requested quantity-side state is already active."""


class SalesReturnWarehouseQuantityStateError(
    SalesReturnWarehouseQuantityError
):
    """Persisted quantity-side state is inconsistent."""


@dataclass(
    frozen=True,
    slots=True,
)
class SalesReturnWarehouseQuantityContext:
    event: TradeReturnEvent
    document: Document
    line: DocumentLine


def _decimal(
    value,
) -> Decimal:
    return Decimal(
        str(
            value
        )
    )


def _enum_value(
    value,
) -> str:
    return str(
        getattr(
            value,
            "value",
            value,
        )
    ).strip().lower()


def _validate_event_identity(
    event: TradeReturnEvent,
) -> None:
    if (
        event.id is None
        or event.id <= 0
    ):
        raise SalesReturnWarehouseQuantitySourceError(
            "TradeReturnEvent must have "
            "a persistent positive ID"
        )

    if (
        event.company_id is None
        or event.company_id <= 0
    ):
        raise SalesReturnWarehouseQuantitySourceError(
            "TradeReturnEvent company_id "
            "must be positive"
        )

    if (
        _enum_value(
            event.direction
        )
        != "sale"
    ):
        raise SalesReturnWarehouseQuantitySourceError(
            "Warehouse quantity runtime requires "
            "a sales TradeReturnEvent"
        )

    if (
        _enum_value(
            event.return_document_type
        )
        != "receipt"
    ):
        raise SalesReturnWarehouseQuantitySourceError(
            "Sales Return warehouse quantity runtime "
            "requires a RECEIPT target"
        )

    quantity = _decimal(
        event.returned_quantity
    )

    if quantity <= ZERO:
        raise SalesReturnWarehouseQuantitySourceError(
            "Returned quantity must be positive"
        )


async def _load_sales_return_warehouse_quantity_context(
    db: AsyncSession,
    *,
    event: TradeReturnEvent,
) -> SalesReturnWarehouseQuantityContext:
    """
    Lock exact return Document + DocumentLine provenance.

    One TradeReturnEvent maps to one exact physical return line.
    """

    _validate_event_identity(
        event
    )

    document = (
        await db.execute(
            select(
                Document
            )
            .where(
                Document.company_id
                == event.company_id,
                Document.id
                == event.return_document_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()

    if document is None:
        raise SalesReturnWarehouseQuantitySourceError(
            "Sales Return warehouse document "
            "was not found"
        )

    if (
        document.company_id
        != event.company_id
    ):
        raise SalesReturnWarehouseQuantitySourceError(
            "Sales Return warehouse document "
            "company mismatch"
        )

    if (
        document.document_type
        != DocumentType.RECEIPT
    ):
        raise SalesReturnWarehouseQuantitySourceError(
            "Sales Return warehouse document "
            "must be RECEIPT"
        )

    line = (
        await db.execute(
            select(
                DocumentLine
            )
            .where(
                DocumentLine.document_id
                == event.return_document_id,
                DocumentLine.id
                == event.return_document_line_id,
                DocumentLine.product_id
                == event.product_id,
                DocumentLine.warehouse_id
                == event.return_warehouse_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()

    if line is None:
        raise SalesReturnWarehouseQuantitySourceError(
            "Sales Return warehouse line "
            "was not found with exact provenance"
        )

    if (
        _decimal(
            line.quantity
        )
        != _decimal(
            event.returned_quantity
        )
    ):
        raise SalesReturnWarehouseQuantitySourceError(
            "Return document-line quantity differs "
            "from immutable TradeReturnEvent quantity"
        )

    return (
        SalesReturnWarehouseQuantityContext(
            event=event,
            document=document,
            line=line,
        )
    )


async def _load_sales_return_quantity_history(
    db: AsyncSession,
    *,
    context: SalesReturnWarehouseQuantityContext,
) -> tuple[
    StockLedger,
    ...,
]:
    """
    Lock all stock-ledger history for this exact return line.

    Dedicated Sales Return quantity history may contain only:
      RECEIPT  +qty
      REVERSAL -qty

    This allows immutable:
      original -> reversal -> replacement
    without deleting historical StockLedger rows.
    """

    result = await db.execute(
        select(
            StockLedger
        )
        .where(
            StockLedger.company_id
            == context.event.company_id,
            StockLedger.document_id
            == context.event.return_document_id,
            StockLedger.document_line_id
            == context.event.return_document_line_id,
        )
        .order_by(
            StockLedger.id
        )
        .with_for_update()
    )

    return tuple(
        result.scalars().all()
    )


def _active_sales_return_quantity(
    *,
    context: SalesReturnWarehouseQuantityContext,
    movements: tuple[
        StockLedger,
        ...,
    ],
) -> Decimal:
    """
    Reconstruct active quantity from immutable ledger history.

    Valid states for one return line:
        0
        returned_quantity
    """

    expected_quantity = _decimal(
        context.event.returned_quantity
    )

    active_quantity = ZERO

    for movement in movements:
        if (
            movement.company_id
            != context.event.company_id
            or movement.document_id
            != context.event.return_document_id
            or movement.document_line_id
            != context.event.return_document_line_id
            or movement.product_id
            != context.event.product_id
            or movement.warehouse_id
            != context.event.return_warehouse_id
        ):
            raise SalesReturnWarehouseQuantityStateError(
                "Sales Return StockLedger "
                "provenance mismatch"
            )

        movement_type = _enum_value(
            movement.movement_type
        )

        quantity = _decimal(
            movement.quantity
        )

        if movement_type == "receipt":
            if quantity != expected_quantity:
                raise SalesReturnWarehouseQuantityStateError(
                    "Sales Return RECEIPT ledger quantity "
                    "differs from immutable return quantity"
                )

        elif movement_type == "reversal":
            if quantity != -expected_quantity:
                raise SalesReturnWarehouseQuantityStateError(
                    "Sales Return REVERSAL ledger quantity "
                    "differs from immutable return quantity"
                )

        else:
            raise SalesReturnWarehouseQuantityStateError(
                "Unexpected StockLedger movement type "
                "for Sales Return return line"
            )

        active_quantity += quantity

        if active_quantity not in (
            ZERO,
            expected_quantity,
        ):
            raise SalesReturnWarehouseQuantityStateError(
                "Sales Return quantity history "
                "entered an invalid active state"
            )

    return active_quantity


async def _load_reversed_trade_return_event(
    db: AsyncSession,
    *,
    reversal_event: TradeReturnEvent,
) -> TradeReturnEvent:
    if reversal_event.reversal_of_id is None:
        raise SalesReturnWarehouseQuantitySourceError(
            "Quantity reversal requires a "
            "TradeReturnEvent reversal"
        )

    original = (
        await db.execute(
            select(
                TradeReturnEvent
            )
            .where(
                TradeReturnEvent.company_id
                == reversal_event.company_id,
                TradeReturnEvent.id
                == reversal_event.reversal_of_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()

    if original is None:
        raise SalesReturnWarehouseQuantitySourceError(
            "Reversed TradeReturnEvent "
            "was not found"
        )

    source_fields = (
        "direction",
        "original_fulfillment_id",
        "original_trade_document_id",
        "original_trade_document_line_id",
        "original_fulfillment_line_id",
        "product_id",
        "return_document_id",
        "return_document_type",
        "return_document_line_id",
        "return_warehouse_id",
    )

    for field in source_fields:
        if (
            getattr(
                original,
                field,
            )
            != getattr(
                reversal_event,
                field,
            )
        ):
            raise SalesReturnWarehouseQuantitySourceError(
                "TradeReturnEvent reversal "
                f"changed immutable source field: {field}"
            )

    if (
        _decimal(
            original.returned_quantity
        )
        != _decimal(
            reversal_event.returned_quantity
        )
    ):
        raise SalesReturnWarehouseQuantitySourceError(
            "TradeReturnEvent reversal "
            "changed returned quantity"
        )

    return original


async def post_sales_return_warehouse_quantity(
    db: AsyncSession,
    *,
    event: TradeReturnEvent,
) -> StockLedger:
    """
    Apply quantity side of one original/replacement Sales Return.

    Changes ONLY:
      StockBalance
      StockLedger

    Does NOT:
      run FIFO / moving-average costing
      create InventoryCostEntry
      create JournalEntry
      call post_document()
      change original fulfillment
      COMMIT / ROLLBACK
    """

    _validate_event_identity(
        event
    )

    if event.reversal_of_id is not None:
        raise SalesReturnWarehouseQuantitySourceError(
            "Original quantity posting cannot consume "
            "a TradeReturnEvent reversal"
        )

    context = (
        await _load_sales_return_warehouse_quantity_context(
            db,
            event=event,
        )
    )

    await ensure_period_open(
        company_id=event.company_id,
        operation_date=event.return_date,
        db=db,
    )

    history = (
        await _load_sales_return_quantity_history(
            db,
            context=context,
        )
    )

    active_quantity = (
        _active_sales_return_quantity(
            context=context,
            movements=history,
        )
    )

    quantity = _decimal(
        event.returned_quantity
    )

    if active_quantity == quantity:
        raise SalesReturnWarehouseQuantityDuplicateError(
            "Sales Return warehouse quantity "
            "is already active"
        )

    if active_quantity != ZERO:
        raise SalesReturnWarehouseQuantityStateError(
            "Sales Return warehouse quantity "
            "history is inconsistent"
        )

    balance = await get_locked_stock_balance(
        db=db,
        company_id=event.company_id,
        product_id=event.product_id,
        warehouse_id=event.return_warehouse_id,
    )

    balance.quantity = (
        _decimal(
            balance.quantity
        )
        + quantity
    )

    now = datetime.now(
        timezone.utc
    ).replace(
        tzinfo=None
    )

    balance.updated_at = now

    movement = StockLedger(
        company_id=event.company_id,
        document_id=event.return_document_id,
        document_line_id=event.return_document_line_id,
        product_id=event.product_id,
        warehouse_id=event.return_warehouse_id,
        quantity=quantity,
        movement_type=(
            StockMovementType.RECEIPT
        ),
        movement_date=event.return_date,
    )

    db.add(
        movement
    )

    return movement


async def reverse_sales_return_warehouse_quantity(
    db: AsyncSession,
    *,
    reversal_event: TradeReturnEvent,
) -> StockLedger:
    """
    Reverse quantity side only for an ACTUAL physical
    TradeReturnEvent reversal/cancellation.

    Cost/economic immutable replacement events do not call this.

    Changes ONLY:
      StockBalance
      StockLedger REVERSAL
    """

    _validate_event_identity(
        reversal_event
    )

    await _load_reversed_trade_return_event(
        db,
        reversal_event=reversal_event,
    )

    context = (
        await _load_sales_return_warehouse_quantity_context(
            db,
            event=reversal_event,
        )
    )

    await ensure_period_open(
        company_id=reversal_event.company_id,
        operation_date=reversal_event.return_date,
        db=db,
    )

    history = (
        await _load_sales_return_quantity_history(
            db,
            context=context,
        )
    )

    active_quantity = (
        _active_sales_return_quantity(
            context=context,
            movements=history,
        )
    )

    quantity = _decimal(
        reversal_event.returned_quantity
    )

    if active_quantity == ZERO:
        raise SalesReturnWarehouseQuantityDuplicateError(
            "Sales Return warehouse quantity "
            "is already reversed"
        )

    if active_quantity != quantity:
        raise SalesReturnWarehouseQuantityStateError(
            "Sales Return warehouse quantity "
            "history is inconsistent before reversal"
        )

    balance = await get_locked_stock_balance(
        db=db,
        company_id=reversal_event.company_id,
        product_id=reversal_event.product_id,
        warehouse_id=(
            reversal_event.return_warehouse_id
        ),
    )

    current_balance = _decimal(
        balance.quantity
    )

    if current_balance < quantity:
        raise SalesReturnWarehouseQuantityStateError(
            "Cannot reverse Sales Return quantity "
            "because warehouse balance would become negative"
        )

    balance.quantity = (
        current_balance
        - quantity
    )

    now = datetime.now(
        timezone.utc
    ).replace(
        tzinfo=None
    )

    balance.updated_at = now

    movement = StockLedger(
        company_id=reversal_event.company_id,
        document_id=(
            reversal_event.return_document_id
        ),
        document_line_id=(
            reversal_event.return_document_line_id
        ),
        product_id=reversal_event.product_id,
        warehouse_id=(
            reversal_event.return_warehouse_id
        ),
        quantity=-quantity,
        movement_type=(
            StockMovementType.REVERSAL
        ),
        movement_date=(
            reversal_event.return_date
        ),
    )

    db.add(
        movement
    )

    return movement


async def apply_sales_return_warehouse_quantity_event(
    db: AsyncSession,
    *,
    event: TradeReturnEvent,
) -> StockLedger:
    """
    Dispatch one immutable TradeReturnEvent to quantity runtime.

    Original/replacement:
        +StockBalance
        StockLedger RECEIPT

    Reversal:
        -StockBalance
        StockLedger REVERSAL
    """

    if event.reversal_of_id is not None:
        return (
            await reverse_sales_return_warehouse_quantity(
                db,
                reversal_event=event,
            )
        )

    return (
        await post_sales_return_warehouse_quantity(
            db,
            event=event,
        )
    )
