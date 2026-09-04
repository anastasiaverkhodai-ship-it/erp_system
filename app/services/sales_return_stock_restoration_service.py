from datetime import (
    datetime,
    timezone,
)
from decimal import (
    Decimal,
    ROUND_HALF_UP,
)
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.company import (
    InventoryValuationMethod,
)
from app.models.document import (
    Document,
    DocumentType,
)
from app.models.document_line import (
    DocumentLine,
)
from app.models.moving_average_movement import (
    MovingAverageMovement,
)
from app.models.sales_return_cost_restoration_event import (
    SalesReturnCostRestorationEvent,
)
from app.models.sales_return_cost_restoration_fifo_slice import (
    SalesReturnCostRestorationFifoSlice,
)
from app.models.stock_ledger import (
    StockMovementType,
)
from app.models.stock_lot import (
    StockLot,
)
from app.models.trade_return_event import (
    TradeReturnEvent,
)
from app.services.moving_average_inventory import (
    MovingAverageInventoryError,
    get_locked_moving_average_balance,
)


ZERO = Decimal("0")

FIFO_UNIT_COST_QUANTUM = Decimal(
    "0.0001"
)

VALUATION_QUANTUM = Decimal(
    "0.00000001"
)


class SalesReturnStockRestorationError(
    Exception
):
    """Base Sales Return stock-restoration error."""


class SalesReturnStockRestorationDataIntegrityError(
    SalesReturnStockRestorationError
):
    """Immutable source and physical return state disagree."""


class SalesReturnStockRestorationDuplicateError(
    SalesReturnStockRestorationError
):
    """Physical restoration already exists."""


def _decimal(
    value,
) -> Decimal:
    return Decimal(
        str(
            value
        )
    )


def _valuation(
    value,
) -> Decimal:
    return _decimal(
        value
    ).quantize(
        VALUATION_QUANTUM,
        rounding=ROUND_HALF_UP,
    )


def _fifo_unit_cost(
    value,
) -> Decimal:
    """
    StockLot.unit_cost is NUMERIC(18,4).

    This is the physical FIFO lot representation only.
    The exact restored historical valuation remains stored
    independently by SalesReturnCostRestorationEvent.
    """

    return _decimal(
        value
    ).quantize(
        FIFO_UNIT_COST_QUANTUM,
        rounding=ROUND_HALF_UP,
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


def validate_sales_return_stock_restoration_source(
    *,
    document: Document,
    line: DocumentLine,
    trade_return_event: TradeReturnEvent,
    cost_event: SalesReturnCostRestorationEvent,
    fifo_slices: Iterable[
        SalesReturnCostRestorationFifoSlice
    ] = (),
) -> None:
    if (
        document.document_type
        != DocumentType.RECEIPT
    ):
        raise SalesReturnStockRestorationDataIntegrityError(
            "Sales Return stock restoration "
            "requires a RECEIPT document"
        )

    if (
        _enum_value(
            trade_return_event.direction
        )
        != "sale"
    ):
        raise SalesReturnStockRestorationDataIntegrityError(
            "Stock restoration source must be "
            "a sales TradeReturnEvent"
        )

    if (
        _enum_value(
            trade_return_event.return_document_type
        )
        != "receipt"
    ):
        raise SalesReturnStockRestorationDataIntegrityError(
            "Sales return must reference "
            "a RECEIPT document"
        )

    if (
        trade_return_event.company_id
        != document.company_id
    ):
        raise SalesReturnStockRestorationDataIntegrityError(
            "Trade Return company mismatch"
        )

    if (
        trade_return_event.return_document_id
        != document.id
    ):
        raise SalesReturnStockRestorationDataIntegrityError(
            "Trade Return document mismatch"
        )

    if (
        trade_return_event.return_document_line_id
        != line.id
    ):
        raise SalesReturnStockRestorationDataIntegrityError(
            "Trade Return document-line mismatch"
        )

    if (
        trade_return_event.product_id
        != line.product_id
    ):
        raise SalesReturnStockRestorationDataIntegrityError(
            "Trade Return product mismatch"
        )

    if (
        trade_return_event.return_warehouse_id
        != line.warehouse_id
    ):
        raise SalesReturnStockRestorationDataIntegrityError(
            "Trade Return warehouse mismatch"
        )

    if (
        trade_return_event.return_date
        != document.document_date
    ):
        raise SalesReturnStockRestorationDataIntegrityError(
            "Trade Return date mismatch"
        )

    if (
        _decimal(
            trade_return_event.returned_quantity
        )
        != _decimal(
            line.quantity
        )
    ):
        raise SalesReturnStockRestorationDataIntegrityError(
            "Trade Return quantity mismatch"
        )

    if (
        cost_event.trade_return_event_id
        != trade_return_event.id
    ):
        raise SalesReturnStockRestorationDataIntegrityError(
            "Cost-restoration source mismatch"
        )

    if (
        _decimal(
            cost_event.restored_quantity
        )
        != _decimal(
            trade_return_event.returned_quantity
        )
    ):
        raise SalesReturnStockRestorationDataIntegrityError(
            "Restored quantity mismatch"
        )

    if (
        cost_event.restoration_date
        != trade_return_event.return_date
    ):
        raise SalesReturnStockRestorationDataIntegrityError(
            "Cost-restoration date mismatch"
        )

    method = _enum_value(
        cost_event.valuation_method
    )

    slices = tuple(
        fifo_slices
    )

    if method == "fifo":
        if not slices:
            raise SalesReturnStockRestorationDataIntegrityError(
                "FIFO restoration requires "
                "immutable FIFO provenance"
            )

        slice_quantity = sum(
            (
                _decimal(
                    item.restored_quantity
                )
                for item in slices
            ),
            ZERO,
        )

        slice_valuation = sum(
            (
                _decimal(
                    item.restored_valuation_amount
                )
                for item in slices
            ),
            ZERO,
        )

        if (
            slice_quantity
            != _decimal(
                cost_event.restored_quantity
            )
        ):
            raise SalesReturnStockRestorationDataIntegrityError(
                "FIFO slice quantity does not "
                "match restoration parent"
            )

        if (
            _valuation(
                slice_valuation
            )
            != _valuation(
                cost_event.restored_valuation_amount
            )
        ):
            raise SalesReturnStockRestorationDataIntegrityError(
                "FIFO slice valuation does not "
                "match restoration parent"
            )

        return

    if method == "weighted_average_moving":
        if slices:
            raise SalesReturnStockRestorationDataIntegrityError(
                "Moving-average restoration cannot "
                "contain FIFO provenance"
            )

        return

    raise SalesReturnStockRestorationDataIntegrityError(
        "Unsupported valuation method"
    )


async def restore_sales_return_fifo_stock(
    db: AsyncSession,
    *,
    document: Document,
    line: DocumentLine,
    cost_event: SalesReturnCostRestorationEvent,
) -> StockLot:
    """
    Apply one active FIFO Sales Return cost state.

    First application:
        create one physical return StockLot.

    Replacement after immutable cost reversal:
        reactivate the SAME StockLot row because
        source_document_line_id is unique.

    Exact accounting/valuation total remains authoritative on
    SalesReturnCostRestorationEvent. StockLot.unit_cost remains
    schema-native NUMERIC(18,4).

    No StockBalance / StockLedger mutation.
    """

    quantity = _decimal(
        cost_event.restored_quantity
    )

    if quantity <= ZERO:
        raise SalesReturnStockRestorationDataIntegrityError(
            "Restored FIFO quantity must be positive"
        )

    unit_cost = _fifo_unit_cost(
        cost_event.aggregate_historical_unit_cost
    )

    if unit_cost < ZERO:
        raise SalesReturnStockRestorationDataIntegrityError(
            "FIFO unit cost cannot be negative"
        )

    existing = (
        await db.execute(
            select(
                StockLot
            )
            .where(
                StockLot.company_id
                == document.company_id,
                StockLot.source_document_line_id
                == line.id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()

    if existing is None:
        stock_lot = StockLot(
            company_id=document.company_id,
            product_id=line.product_id,
            warehouse_id=line.warehouse_id,
            source_document_id=document.id,
            source_document_line_id=line.id,
            received_date=document.document_date,
            original_quantity=quantity,
            remaining_quantity=quantity,
            unit_cost=unit_cost,
        )

        db.add(
            stock_lot
        )

        return stock_lot

    if (
        existing.product_id
        != line.product_id
        or existing.warehouse_id
        != line.warehouse_id
        or existing.source_document_id
        != document.id
    ):
        raise SalesReturnStockRestorationDataIntegrityError(
            "Existing Sales Return FIFO lot "
            "provenance mismatch"
        )

    original_quantity = _decimal(
        existing.original_quantity
    )

    remaining_quantity = _decimal(
        existing.remaining_quantity
    )

    if remaining_quantity != ZERO:
        raise SalesReturnStockRestorationDuplicateError(
            "Active FIFO StockLot already exists for "
            "Sales Return document line"
        )

    if original_quantity != quantity:
        raise SalesReturnStockRestorationDataIntegrityError(
            "Reactivated FIFO lot quantity differs from "
            "immutable Sales Return quantity"
        )

    existing.remaining_quantity = (
        quantity
    )

    existing.unit_cost = (
        unit_cost
    )

    existing.received_date = (
        document.document_date
    )

    return existing

def _active_sales_return_moving_average_originals(
    movements,
):
    """
    Reconstruct active non-reversal movements from immutable
    MovingAverageMovement original/reversal history.

    Historical originals remain persisted; an original is active
    only while no movement references it through reversal_of_id.
    """

    values = tuple(
        movements
    )

    ids = {
        movement.id
        for movement in values
        if movement.id is not None
    }

    if len(
        ids
    ) != len(
        values
    ):
        raise SalesReturnStockRestorationDataIntegrityError(
            "Moving-average Sales Return history contains "
            "missing or duplicate movement IDs"
        )

    by_id = {
        movement.id: movement
        for movement in values
    }

    reversed_ids = set()

    for movement in values:
        if movement.reversal_of_id is None:
            continue

        original = by_id.get(
            movement.reversal_of_id
        )

        if original is None:
            raise SalesReturnStockRestorationDataIntegrityError(
                "Moving-average reversal references an "
                "unloaded Sales Return movement"
            )

        reversed_ids.add(
            original.id
        )

    return tuple(
        movement
        for movement in values
        if (
            movement.reversal_of_id is None
            and movement.id
            not in reversed_ids
        )
    )


async def _load_sales_return_moving_average_line_history(
    db: AsyncSession,
    *,
    company_id: int,
    document_line_id: int,
):
    result = await db.execute(
        select(
            MovingAverageMovement
        )
        .where(
            MovingAverageMovement.company_id
            == company_id,
            MovingAverageMovement.document_line_id
            == document_line_id,
        )
        .order_by(
            MovingAverageMovement.id
        )
        .with_for_update()
    )

    return tuple(
        result.scalars().all()
    )


async def restore_sales_return_moving_average_stock(
    db: AsyncSession,
    *,
    document: Document,
    line: DocumentLine,
    cost_event: SalesReturnCostRestorationEvent,
) -> MovingAverageMovement:
    """
    Apply one active moving-average Sales Return cost state.

    An immutable replacement is allowed only after the previous
    original movement has itself been reversed.

    Each replacement creates a NEW original receipt movement,
    preserving original/reversal history.
    """

    history = (
        await _load_sales_return_moving_average_line_history(
            db,
            company_id=document.company_id,
            document_line_id=line.id,
        )
    )

    active = (
        _active_sales_return_moving_average_originals(
            history
        )
    )

    if len(
        active
    ) > 1:
        raise SalesReturnStockRestorationDataIntegrityError(
            "Multiple active moving-average Sales Return "
            "movements exist for one document line"
        )

    if active:
        raise SalesReturnStockRestorationDuplicateError(
            "Active moving-average movement already exists "
            "for Sales Return document line"
        )

    quantity = _decimal(
        cost_event.restored_quantity
    )

    restored_value = _valuation(
        cost_event.restored_valuation_amount
    )

    historical_unit_cost = _valuation(
        cost_event.aggregate_historical_unit_cost
    )

    if quantity <= ZERO:
        raise SalesReturnStockRestorationDataIntegrityError(
            "Restored moving-average quantity "
            "must be positive"
        )

    if restored_value < ZERO:
        raise SalesReturnStockRestorationDataIntegrityError(
            "Restored moving-average value "
            "cannot be negative"
        )

    if historical_unit_cost < ZERO:
        raise SalesReturnStockRestorationDataIntegrityError(
            "Moving-average historical unit cost "
            "cannot be negative"
        )

    try:
        balance = (
            await get_locked_moving_average_balance(
                db=db,
                company_id=document.company_id,
                product_id=line.product_id,
                warehouse_id=line.warehouse_id,
            )
        )
    except MovingAverageInventoryError as exc:
        raise SalesReturnStockRestorationError(
            str(
                exc
            )
        ) from exc

    old_quantity = _decimal(
        balance.quantity
    )

    old_value = _valuation(
        balance.inventory_value
    )

    if (
        old_quantity == ZERO
        and old_value != ZERO
    ):
        raise SalesReturnStockRestorationDataIntegrityError(
            "Moving-average balance is inconsistent: "
            "zero quantity has non-zero value"
        )

    new_quantity = (
        old_quantity
        + quantity
    )

    new_value = _valuation(
        old_value
        + restored_value
    )

    new_average = _valuation(
        new_value
        / new_quantity
    )

    balance.quantity = (
        new_quantity
    )

    balance.inventory_value = (
        new_value
    )

    balance.average_unit_cost = (
        new_average
    )

    balance.updated_at = datetime.now(
        timezone.utc
    ).replace(
        tzinfo=None
    )

    movement = MovingAverageMovement(
        company_id=document.company_id,
        document_id=document.id,
        document_line_id=line.id,
        product_id=line.product_id,
        warehouse_id=line.warehouse_id,
        movement_type=(
            StockMovementType.RECEIPT
        ),
        movement_date=document.document_date,
        quantity_delta=quantity,
        value_delta=restored_value,
        unit_cost=historical_unit_cost,
        balance_quantity_after=(
            new_quantity
        ),
        balance_value_after=(
            new_value
        ),
        average_unit_cost_after=(
            new_average
        ),
    )

    db.add(
        movement
    )

    return movement


async def reverse_sales_return_fifo_cost_state(
    db: AsyncSession,
    *,
    document: Document,
    line: DocumentLine,
    reversal_event: SalesReturnCostRestorationEvent,
) -> StockLot:
    """
    Reverse the active FIFO return cost state for one return line.

    This is line-scoped, unlike generic reverse_receipt_fifo().

    The lot must be completely unconsumed. Reversal makes the
    returned FIFO quantity inactive by setting remaining_quantity
    to zero. A following immutable replacement reactivates this
    same row.

    No StockBalance / StockLedger mutation.
    """

    if reversal_event.reversal_of_id is None:
        raise SalesReturnStockRestorationDataIntegrityError(
            "FIFO physical cost reversal requires an "
            "immutable reversal cost event"
        )

    lot = (
        await db.execute(
            select(
                StockLot
            )
            .where(
                StockLot.company_id
                == document.company_id,
                StockLot.source_document_line_id
                == line.id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()

    if lot is None:
        raise SalesReturnStockRestorationDataIntegrityError(
            "Active Sales Return FIFO lot was not found"
        )

    if (
        lot.product_id
        != line.product_id
        or lot.warehouse_id
        != line.warehouse_id
        or lot.source_document_id
        != document.id
    ):
        raise SalesReturnStockRestorationDataIntegrityError(
            "Sales Return FIFO lot provenance mismatch"
        )

    original_quantity = _decimal(
        lot.original_quantity
    )

    remaining_quantity = _decimal(
        lot.remaining_quantity
    )

    expected_quantity = _decimal(
        reversal_event.restored_quantity
    )

    expected_unit_cost = _fifo_unit_cost(
        reversal_event.aggregate_historical_unit_cost
    )

    if original_quantity != expected_quantity:
        raise SalesReturnStockRestorationDataIntegrityError(
            "FIFO reversal quantity differs from "
            "immutable cost event"
        )

    if (
        _decimal(
            lot.unit_cost
        )
        != expected_unit_cost
    ):
        raise SalesReturnStockRestorationDataIntegrityError(
            "FIFO active lot cost differs from "
            "immutable reversal event"
        )

    if remaining_quantity == ZERO:
        raise SalesReturnStockRestorationDuplicateError(
            "Sales Return FIFO cost state "
            "has already been reversed"
        )

    if remaining_quantity != original_quantity:
        raise SalesReturnStockRestorationDataIntegrityError(
            "Cannot reverse Sales Return FIFO cost state "
            "after the returned lot has been consumed"
        )

    lot.remaining_quantity = (
        ZERO
    )

    return lot


async def reverse_sales_return_moving_average_cost_state(
    db: AsyncSession,
    *,
    document: Document,
    line: DocumentLine,
    reversal_event: SalesReturnCostRestorationEvent,
) -> MovingAverageMovement:
    """
    Reverse exactly ONE active moving-average Sales Return
    movement for one return document line.

    This intentionally does NOT reuse the generic document-wide
    reverse_moving_average_document().

    Safety:
    - one active original movement only;
    - it must be the latest inventory movement for the same
      product + warehouse;
    - current balance must exactly equal its after-state.

    A following replacement can then create a new original
    receipt movement.
    """

    if reversal_event.reversal_of_id is None:
        raise SalesReturnStockRestorationDataIntegrityError(
            "Moving-average physical cost reversal requires "
            "an immutable reversal cost event"
        )

    history = (
        await _load_sales_return_moving_average_line_history(
            db,
            company_id=document.company_id,
            document_line_id=line.id,
        )
    )

    active = (
        _active_sales_return_moving_average_originals(
            history
        )
    )

    if not active:
        raise SalesReturnStockRestorationDuplicateError(
            "No active moving-average Sales Return "
            "cost movement remains to reverse"
        )

    if len(
        active
    ) != 1:
        raise SalesReturnStockRestorationDataIntegrityError(
            "Expected exactly one active moving-average "
            "Sales Return movement"
        )

    movement = active[
        0
    ]

    if (
        movement.document_id
        != document.id
        or movement.document_line_id
        != line.id
        or movement.product_id
        != line.product_id
        or movement.warehouse_id
        != line.warehouse_id
    ):
        raise SalesReturnStockRestorationDataIntegrityError(
            "Moving-average Sales Return "
            "movement provenance mismatch"
        )

    if (
        _decimal(
            movement.quantity_delta
        )
        != _decimal(
            reversal_event.restored_quantity
        )
    ):
        raise SalesReturnStockRestorationDataIntegrityError(
            "Moving-average reversal quantity differs "
            "from immutable cost event"
        )

    if (
        _valuation(
            movement.value_delta
        )
        != _valuation(
            reversal_event.restored_valuation_amount
        )
    ):
        raise SalesReturnStockRestorationDataIntegrityError(
            "Moving-average reversal valuation differs "
            "from immutable cost event"
        )

    if (
        _valuation(
            movement.unit_cost
        )
        != _valuation(
            reversal_event.aggregate_historical_unit_cost
        )
    ):
        raise SalesReturnStockRestorationDataIntegrityError(
            "Moving-average reversal unit cost differs "
            "from immutable cost event"
        )

    latest = (
        await db.execute(
            select(
                MovingAverageMovement
            )
            .where(
                MovingAverageMovement.company_id
                == document.company_id,
                MovingAverageMovement.product_id
                == line.product_id,
                MovingAverageMovement.warehouse_id
                == line.warehouse_id,
            )
            .order_by(
                MovingAverageMovement.id.desc()
            )
            .limit(
                1
            )
            .with_for_update()
        )
    ).scalar_one_or_none()

    if (
        latest is None
        or latest.id
        != movement.id
    ):
        raise SalesReturnStockRestorationDataIntegrityError(
            "Cannot reverse Sales Return moving-average "
            "cost state because later inventory movements exist"
        )

    try:
        balance = (
            await get_locked_moving_average_balance(
                db=db,
                company_id=document.company_id,
                product_id=line.product_id,
                warehouse_id=line.warehouse_id,
            )
        )
    except MovingAverageInventoryError as exc:
        raise SalesReturnStockRestorationError(
            str(
                exc
            )
        ) from exc

    if (
        _decimal(
            balance.quantity
        )
        != _decimal(
            movement.balance_quantity_after
        )
        or _valuation(
            balance.inventory_value
        )
        != _valuation(
            movement.balance_value_after
        )
        or _valuation(
            balance.average_unit_cost
        )
        != _valuation(
            movement.average_unit_cost_after
        )
    ):
        raise SalesReturnStockRestorationDataIntegrityError(
            "Moving-average balance does not match "
            "active Sales Return movement history"
        )

    previous = (
        await db.execute(
            select(
                MovingAverageMovement
            )
            .where(
                MovingAverageMovement.company_id
                == document.company_id,
                MovingAverageMovement.product_id
                == line.product_id,
                MovingAverageMovement.warehouse_id
                == line.warehouse_id,
                MovingAverageMovement.id
                < movement.id,
            )
            .order_by(
                MovingAverageMovement.id.desc()
            )
            .limit(
                1
            )
        )
    ).scalar_one_or_none()

    if previous is None:
        restored_quantity = ZERO
        restored_value = ZERO
        restored_average = ZERO
    else:
        restored_quantity = _decimal(
            previous.balance_quantity_after
        )

        restored_value = _valuation(
            previous.balance_value_after
        )

        restored_average = _valuation(
            previous.average_unit_cost_after
        )

    balance.quantity = (
        restored_quantity
    )

    balance.inventory_value = (
        restored_value
    )

    balance.average_unit_cost = (
        restored_average
    )

    balance.updated_at = datetime.now(
        timezone.utc
    ).replace(
        tzinfo=None
    )

    reversal = MovingAverageMovement(
        company_id=document.company_id,
        document_id=document.id,
        document_line_id=line.id,
        product_id=line.product_id,
        warehouse_id=line.warehouse_id,
        movement_type=(
            StockMovementType.REVERSAL
        ),
        movement_date=(
            reversal_event.restoration_date
        ),
        quantity_delta=(
            -_decimal(
                movement.quantity_delta
            )
        ),
        value_delta=(
            -_valuation(
                movement.value_delta
            )
        ),
        unit_cost=_valuation(
            movement.unit_cost
        ),
        balance_quantity_after=(
            restored_quantity
        ),
        balance_value_after=(
            restored_value
        ),
        average_unit_cost_after=(
            restored_average
        ),
        reversal_of_id=(
            movement.id
        ),
    )

    db.add(
        reversal
    )

    return reversal


async def reverse_sales_return_physical_cost_state(
    db: AsyncSession,
    *,
    document: Document,
    line: DocumentLine,
    reversal_event: SalesReturnCostRestorationEvent,
):
    """
    Reverse valuation-specific Sales Return cost state only.

    FIFO:
        deactivate one unconsumed return StockLot.

    Moving average:
        reverse one active line-scoped receipt movement.

    No StockBalance.
    No StockLedger.
    No JournalEntry.
    No COMMIT / ROLLBACK.
    """

    if reversal_event.reversal_of_id is None:
        raise SalesReturnStockRestorationDataIntegrityError(
            "Physical cost reversal requires a "
            "SalesReturnCostRestorationEvent reversal"
        )

    if (
        reversal_event.company_id
        != document.company_id
    ):
        raise SalesReturnStockRestorationDataIntegrityError(
            "Physical cost reversal company mismatch"
        )

    method = _enum_value(
        reversal_event.valuation_method
    )

    if method == "fifo":
        return await reverse_sales_return_fifo_cost_state(
            db,
            document=document,
            line=line,
            reversal_event=reversal_event,
        )

    if method == "weighted_average_moving":
        return (
            await reverse_sales_return_moving_average_cost_state(
                db,
                document=document,
                line=line,
                reversal_event=reversal_event,
            )
        )

    raise SalesReturnStockRestorationDataIntegrityError(
        "Unsupported Sales Return reversal "
        "valuation method"
    )

async def restore_sales_return_physical_cost_state(
    db: AsyncSession,
    *,
    document: Document,
    line: DocumentLine,
    trade_return_event: TradeReturnEvent,
    cost_event: SalesReturnCostRestorationEvent,
    fifo_slices: Iterable[
        SalesReturnCostRestorationFifoSlice
    ] = (),
):
    """
    Restore valuation-specific physical state only.

    IMPORTANT:
        StockBalance quantity and StockLedger ownership remain
        with the warehouse posting layer.

    This service does NOT:
        - mutate StockBalance quantity
        - create StockLedger
        - create JournalEntry
        - commit / rollback
    """

    slices = tuple(
        fifo_slices
    )

    validate_sales_return_stock_restoration_source(
        document=document,
        line=line,
        trade_return_event=trade_return_event,
        cost_event=cost_event,
        fifo_slices=slices,
    )

    method = _enum_value(
        cost_event.valuation_method
    )

    if method == "fifo":
        return await restore_sales_return_fifo_stock(
            db,
            document=document,
            line=line,
            cost_event=cost_event,
        )

    if method == "weighted_average_moving":
        return (
            await restore_sales_return_moving_average_stock(
                db,
                document=document,
                line=line,
                cost_event=cost_event,
            )
        )

    raise SalesReturnStockRestorationDataIntegrityError(
        "Unsupported Sales Return valuation method"
    )
