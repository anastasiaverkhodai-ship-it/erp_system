from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.inventory_cost_entry import (
    InventoryCostEntry,
)
from app.models.sales_return_cost_restoration_event import (
    SalesReturnCostRestorationEvent,
)
from app.models.sales_return_cost_restoration_fifo_slice import (
    SalesReturnCostRestorationFifoSlice,
)
from app.models.stock_lot_consumption import (
    StockLotConsumption,
)
from app.models.trade_fulfillment_line import (
    TradeFulfillmentLine,
)
from app.models.trade_return_event import (
    TradeReturnEvent,
)
from app.services.sales_return_cost_calculation_service import (
    FIFO,
    WEIGHTED_AVERAGE_MOVING,
    SalesReturnCostTarget,
    SalesReturnFifoSliceTarget,
)


ZERO = Decimal("0")


class SalesReturnCostRestorationPersistenceError(
    Exception
):
    """Base Sales Return cost-restoration persistence error."""


class SalesReturnCostRestorationSourceNotFoundError(
    SalesReturnCostRestorationPersistenceError
):
    """Required immutable source was not found."""


class SalesReturnCostRestorationSourceStateError(
    SalesReturnCostRestorationPersistenceError
):
    """Source state cannot support the requested restoration."""


class SalesReturnCostRestorationDataIntegrityError(
    SalesReturnCostRestorationPersistenceError
):
    """Persistent cost-restoration history is inconsistent."""


@dataclass(
    frozen=True,
    slots=True,
)
class SalesReturnCostRestorationSourcePlan:
    """
    Immutable persistence action for one pair:

        TradeReturnEvent
        +
        InventoryCostEntry

    replacement_target is always the COMPLETE desired state.

    New positive target:
        create original.

    Exact current target:
        no-op.

    Changed positive target:
        reverse active original
        +
        create full replacement.

    Zero target:
        reverse active original only.
    """

    reversal_event_ids: tuple[
        int,
        ...,
    ]

    replacement_target: (
        SalesReturnCostTarget
        | None
    )


def _method_value(
    value,
) -> str:
    return str(
        getattr(
            value,
            "value",
            value,
        )
    ).strip().lower()


def _decimal(
    value,
) -> Decimal:
    return Decimal(
        str(
            value
        )
    )


def _target_is_zero(
    target: SalesReturnCostTarget,
) -> bool:
    return (
        _decimal(
            target.restored_quantity
        )
        == ZERO
    )


def _target_is_positive(
    target: SalesReturnCostTarget,
) -> bool:
    return (
        _decimal(
            target.restored_quantity
        )
        > ZERO
    )


def _validate_zero_target(
    target: SalesReturnCostTarget,
) -> None:
    if not _target_is_zero(
        target
    ):
        return

    if (
        _decimal(
            target.restored_valuation_amount
        )
        != ZERO
    ):
        raise SalesReturnCostRestorationDataIntegrityError(
            "Zero restoration target must have "
            "zero valuation amount"
        )

    if (
        _decimal(
            target.restored_cost_amount
        )
        != ZERO
    ):
        raise SalesReturnCostRestorationDataIntegrityError(
            "Zero restoration target must have "
            "zero accounting cost"
        )

    if (
        _decimal(
            target.aggregate_historical_unit_cost
        )
        != ZERO
    ):
        raise SalesReturnCostRestorationDataIntegrityError(
            "Zero restoration target must have "
            "zero aggregate unit cost"
        )

    if target.fifo_slices:
        raise SalesReturnCostRestorationDataIntegrityError(
            "Zero restoration target cannot have FIFO slices"
        )


def _fifo_target_tuple(
    slices: Iterable[
        SalesReturnCostRestorationFifoSlice
    ],
) -> tuple[
    SalesReturnFifoSliceTarget,
    ...,
]:
    values = tuple(
        sorted(
            slices,
            key=lambda item: (
                item.id or 0
            ),
        )
    )

    return tuple(
        SalesReturnFifoSliceTarget(
            fifo_consumption_id=(
                item.fifo_consumption_id
            ),
            stock_lot_id=(
                item.stock_lot_id
            ),
            quantity=_decimal(
                item.restored_quantity
            ),
            unit_cost=_decimal(
                item.historical_unit_cost
            ),
            valuation_amount=_decimal(
                item.restored_valuation_amount
            ),
        )
        for item in values
    )


def _active_original_events(
    events: Iterable[
        SalesReturnCostRestorationEvent
    ],
) -> tuple[
    SalesReturnCostRestorationEvent,
    ...,
]:
    values = tuple(
        events
    )

    ids = {
        event.id
        for event in values
        if event.id is not None
    }

    if len(
        ids
    ) != len(
        values
    ):
        raise SalesReturnCostRestorationDataIntegrityError(
            "Cost-restoration history contains "
            "missing or duplicate event IDs"
        )

    referenced_ids = set()

    by_id = {
        event.id: event
        for event in values
    }

    for event in values:
        if event.reversal_of_id is None:
            continue

        if (
            event.reversal_of_id
            not in by_id
        ):
            raise SalesReturnCostRestorationDataIntegrityError(
                "Cost-restoration reversal references "
                "an event outside loaded pair history"
            )

        original = by_id[
            event.reversal_of_id
        ]

        if (
            original.trade_return_event_id
            != event.trade_return_event_id
            or original.inventory_cost_entry_id
            != event.inventory_cost_entry_id
        ):
            raise SalesReturnCostRestorationDataIntegrityError(
                "Cost-restoration reversal changed "
                "pair provenance"
            )

        referenced_ids.add(
            event.reversal_of_id
        )

    active = tuple(
        sorted(
            (
                event
                for event in values
                if (
                    event.reversal_of_id
                    is None
                    and event.id
                    not in referenced_ids
                )
            ),
            key=lambda event: event.id,
        )
    )

    return active


def build_current_sales_return_cost_restoration_targets(
    *,
    events: Iterable[
        SalesReturnCostRestorationEvent
    ],
    fifo_slices: Iterable[
        SalesReturnCostRestorationFifoSlice
    ] = (),
) -> tuple[
    SalesReturnCostTarget,
    ...,
]:
    """
    Reconstruct current active desired-state equivalents from
    immutable parent + reversal history.
    """

    event_values = tuple(
        events
    )

    slice_values = tuple(
        fifo_slices
    )

    event_ids = {
        event.id
        for event in event_values
    }

    slices_by_event: dict[
        int,
        list[
            SalesReturnCostRestorationFifoSlice
        ],
    ] = {}

    for item in slice_values:
        parent_id = (
            item.sales_return_cost_restoration_event_id
        )

        if parent_id not in event_ids:
            raise SalesReturnCostRestorationDataIntegrityError(
                "FIFO restoration slice references "
                "an unloaded parent event"
            )

        slices_by_event.setdefault(
            parent_id,
            [],
        ).append(
            item
        )

    active = _active_original_events(
        event_values
    )

    seen_pairs = set()
    result = []

    for event in active:
        pair = (
            event.trade_return_event_id,
            event.inventory_cost_entry_id,
        )

        if pair in seen_pairs:
            raise SalesReturnCostRestorationDataIntegrityError(
                "Multiple active original cost-restoration "
                "events exist for one source pair"
            )

        seen_pairs.add(
            pair
        )

        method = _method_value(
            event.valuation_method
        )

        child_targets = _fifo_target_tuple(
            slices_by_event.get(
                event.id,
                (),
            )
        )

        if (
            method == FIFO
            and not child_targets
        ):
            raise SalesReturnCostRestorationDataIntegrityError(
                "Active FIFO cost-restoration event "
                "has no provenance slices"
            )

        if (
            method
            == WEIGHTED_AVERAGE_MOVING
            and child_targets
        ):
            raise SalesReturnCostRestorationDataIntegrityError(
                "Moving-average cost-restoration event "
                "cannot have FIFO provenance slices"
            )

        result.append(
            SalesReturnCostTarget(
                return_source_id=(
                    event.trade_return_event_id
                ),
                inventory_cost_entry_id=(
                    event.inventory_cost_entry_id
                ),
                event_date=(
                    event.restoration_date
                ),
                valuation_method=method,
                restored_quantity=_decimal(
                    event.restored_quantity
                ),
                restored_valuation_amount=_decimal(
                    event.restored_valuation_amount
                ),
                restored_cost_amount=_decimal(
                    event.restored_cost_amount
                ),
                aggregate_historical_unit_cost=_decimal(
                    event.aggregate_historical_unit_cost
                ),
                fifo_slices=child_targets,
            )
        )

    return tuple(
        sorted(
            result,
            key=lambda target: (
                target.event_date,
                target.return_source_id,
                target.inventory_cost_entry_id,
            ),
        )
    )


def build_sales_return_cost_restoration_source_plan(
    *,
    events: Iterable[
        SalesReturnCostRestorationEvent
    ],
    fifo_slices: Iterable[
        SalesReturnCostRestorationFifoSlice
    ],
    target: SalesReturnCostTarget,
) -> SalesReturnCostRestorationSourcePlan:
    """
    Build one immutable reverse/replacement action.

    target may be an internal zero target used only to remove
    a currently active pair.
    """

    _validate_zero_target(
        target
    )

    if (
        _decimal(
            target.restored_quantity
        )
        < ZERO
    ):
        raise SalesReturnCostRestorationDataIntegrityError(
            "Restored quantity cannot be negative"
        )

    current_targets = (
        build_current_sales_return_cost_restoration_targets(
            events=events,
            fifo_slices=fifo_slices,
        )
    )

    current_matches = tuple(
        current
        for current in current_targets
        if current.pair_key
        == target.pair_key
    )

    if len(
        current_matches
    ) > 1:
        raise SalesReturnCostRestorationDataIntegrityError(
            "Multiple active current targets "
            "exist for one source pair"
        )

    active_events = _active_original_events(
        events
    )

    active_pair_events = tuple(
        event
        for event in active_events
        if (
            event.trade_return_event_id,
            event.inventory_cost_entry_id,
        )
        == target.pair_key
    )

    if len(
        active_pair_events
    ) > 1:
        raise SalesReturnCostRestorationDataIntegrityError(
            "Multiple active originals exist "
            "for one cost-restoration pair"
        )

    current = (
        current_matches[0]
        if current_matches
        else None
    )

    if current is None:
        if _target_is_zero(
            target
        ):
            return (
                SalesReturnCostRestorationSourcePlan(
                    reversal_event_ids=(),
                    replacement_target=None,
                )
            )

        return (
            SalesReturnCostRestorationSourcePlan(
                reversal_event_ids=(),
                replacement_target=target,
            )
        )

    if current == target:
        return (
            SalesReturnCostRestorationSourcePlan(
                reversal_event_ids=(),
                replacement_target=None,
            )
        )

    if (
        current.event_date
        != target.event_date
        and _target_is_positive(
            target
        )
    ):
        raise SalesReturnCostRestorationDataIntegrityError(
            "Restoration date changed unexpectedly "
            "for the same immutable TradeReturn source"
        )

    if (
        current.valuation_method
        != target.valuation_method
        and _target_is_positive(
            target
        )
    ):
        raise SalesReturnCostRestorationDataIntegrityError(
            "Valuation method changed unexpectedly "
            "for the same cost source"
        )

    if len(
        active_pair_events
    ) != 1:
        raise SalesReturnCostRestorationDataIntegrityError(
            "Current target exists without exactly "
            "one active original event"
        )

    reversal_ids = (
        active_pair_events[0].id,
    )

    if _target_is_zero(
        target
    ):
        return (
            SalesReturnCostRestorationSourcePlan(
                reversal_event_ids=(
                    reversal_ids
                ),
                replacement_target=None,
            )
        )

    return (
        SalesReturnCostRestorationSourcePlan(
            reversal_event_ids=(
                reversal_ids
            ),
            replacement_target=target,
        )
    )


async def _load_pair_history(
    db: AsyncSession,
    *,
    company_id: int,
    trade_return_event_id: int,
    inventory_cost_entry_id: int,
    lock: bool,
) -> tuple[
    SalesReturnCostRestorationEvent,
    ...,
]:
    statement = (
        select(
            SalesReturnCostRestorationEvent
        )
        .where(
            (
                SalesReturnCostRestorationEvent
                .company_id
                == company_id
            ),
            (
                SalesReturnCostRestorationEvent
                .trade_return_event_id
                == trade_return_event_id
            ),
            (
                SalesReturnCostRestorationEvent
                .inventory_cost_entry_id
                == inventory_cost_entry_id
            ),
        )
        .order_by(
            SalesReturnCostRestorationEvent.id
        )
    )

    if lock:
        statement = (
            statement.with_for_update()
        )

    return tuple(
        (
            await db.scalars(
                statement
            )
        ).all()
    )


async def _load_fifo_slices_for_events(
    db: AsyncSession,
    *,
    event_ids: Iterable[int],
    lock: bool,
) -> tuple[
    SalesReturnCostRestorationFifoSlice,
    ...,
]:
    ids = tuple(
        sorted(
            {
                int(
                    event_id
                )
                for event_id
                in event_ids
            }
        )
    )

    if not ids:
        return ()

    statement = (
        select(
            SalesReturnCostRestorationFifoSlice
        )
        .where(
            (
                SalesReturnCostRestorationFifoSlice
                .sales_return_cost_restoration_event_id
                .in_(
                    ids
                )
            )
        )
        .order_by(
            (
                SalesReturnCostRestorationFifoSlice
                .sales_return_cost_restoration_event_id
            ),
            SalesReturnCostRestorationFifoSlice.id,
        )
    )

    if lock:
        statement = (
            statement.with_for_update()
        )

    return tuple(
        (
            await db.scalars(
                statement
            )
        ).all()
    )


async def _validate_positive_target_sources(
    db: AsyncSession,
    *,
    company_id: int,
    target: SalesReturnCostTarget,
) -> None:
    if not _target_is_positive(
        target
    ):
        raise SalesReturnCostRestorationDataIntegrityError(
            "Positive-source validation requires "
            "a positive target"
        )

    return_event = (
        await db.execute(
            select(
                TradeReturnEvent
            )
            .where(
                TradeReturnEvent.company_id
                == company_id,
                TradeReturnEvent.id
                == target.return_source_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()

    if return_event is None:
        raise SalesReturnCostRestorationSourceNotFoundError(
            "TradeReturnEvent source was not found"
        )

    if return_event.reversal_of_id is not None:
        raise SalesReturnCostRestorationSourceStateError(
            "A TradeReturnEvent reversal cannot "
            "be a positive cost-restoration source"
        )

    reversal_id = (
        await db.execute(
            select(
                TradeReturnEvent.id
            ).where(
                TradeReturnEvent.company_id
                == company_id,
                TradeReturnEvent.reversal_of_id
                == return_event.id,
            )
        )
    ).scalar_one_or_none()

    if reversal_id is not None:
        raise SalesReturnCostRestorationSourceStateError(
            "TradeReturnEvent source is no longer active"
        )

    direction = _method_value(
        return_event.direction
    )

    if direction != "sale":
        raise SalesReturnCostRestorationSourceStateError(
            "Only sale TradeReturnEvent can restore "
            "sales COGS"
        )

    if (
        return_event.return_date
        != target.event_date
    ):
        raise SalesReturnCostRestorationDataIntegrityError(
            "Target restoration date does not match "
            "TradeReturnEvent.return_date"
        )

    if (
        _decimal(
            return_event.returned_quantity
        )
        != _decimal(
            target.restored_quantity
        )
    ):
        raise SalesReturnCostRestorationDataIntegrityError(
            "Target quantity does not match "
            "TradeReturnEvent quantity"
        )

    fulfillment_line = (
        await db.execute(
            select(
                TradeFulfillmentLine
            )
            .where(
                TradeFulfillmentLine.company_id
                == company_id,
                TradeFulfillmentLine.fulfillment_id
                == (
                    return_event
                    .original_fulfillment_id
                ),
                TradeFulfillmentLine.id
                == (
                    return_event
                    .original_fulfillment_line_id
                ),
            )
            .with_for_update()
        )
    ).scalar_one_or_none()

    if fulfillment_line is None:
        raise SalesReturnCostRestorationSourceNotFoundError(
            "Original TradeFulfillmentLine "
            "was not found"
        )

    if (
        fulfillment_line.product_id
        != return_event.product_id
    ):
        raise SalesReturnCostRestorationDataIntegrityError(
            "Trade Return product does not match "
            "original fulfillment product"
        )

    inventory_cost_entry = (
        await db.execute(
            select(
                InventoryCostEntry
            )
            .where(
                InventoryCostEntry.id
                == target.inventory_cost_entry_id
            )
            .with_for_update()
        )
    ).scalar_one_or_none()

    if inventory_cost_entry is None:
        raise SalesReturnCostRestorationSourceNotFoundError(
            "InventoryCostEntry source was not found"
        )

    if (
        inventory_cost_entry.company_id
        != company_id
    ):
        raise SalesReturnCostRestorationDataIntegrityError(
            "InventoryCostEntry company does not match "
            "cost-restoration company"
        )

    if (
        inventory_cost_entry.document_line_id
        != fulfillment_line.warehouse_document_line_id
    ):
        raise SalesReturnCostRestorationDataIntegrityError(
            "InventoryCostEntry does not belong to "
            "the original fulfillment warehouse ISSUE line"
        )

    source_method = _method_value(
        inventory_cost_entry.valuation_method
    )

    if (
        source_method
        != target.valuation_method
    ):
        raise SalesReturnCostRestorationDataIntegrityError(
            "Target valuation method does not match "
            "InventoryCostEntry"
        )

    if (
        _decimal(
            target.restored_quantity
        )
        > _decimal(
            inventory_cost_entry.quantity
        )
    ):
        raise SalesReturnCostRestorationDataIntegrityError(
            "Restored quantity exceeds "
            "InventoryCostEntry quantity"
        )

    if (
        _decimal(
            target.restored_valuation_amount
        )
        > _decimal(
            inventory_cost_entry.valuation_amount
        )
    ):
        raise SalesReturnCostRestorationDataIntegrityError(
            "Restored valuation exceeds "
            "InventoryCostEntry valuation"
        )

    if (
        _decimal(
            target.restored_cost_amount
        )
        > _decimal(
            inventory_cost_entry.cost_amount
        )
    ):
        raise SalesReturnCostRestorationDataIntegrityError(
            "Restored accounting cost exceeds "
            "InventoryCostEntry cost"
        )

    if source_method == WEIGHTED_AVERAGE_MOVING:
        if target.fifo_slices:
            raise SalesReturnCostRestorationDataIntegrityError(
                "Moving-average target cannot have "
                "FIFO provenance"
            )

        return

    if source_method != FIFO:
        raise SalesReturnCostRestorationDataIntegrityError(
            "Unsupported persistent valuation method"
        )

    if not target.fifo_slices:
        raise SalesReturnCostRestorationDataIntegrityError(
            "FIFO target requires provenance slices"
        )

    total_slice_quantity = sum(
        (
            _decimal(
                item.quantity
            )
            for item in target.fifo_slices
        ),
        ZERO,
    )

    total_slice_valuation = sum(
        (
            _decimal(
                item.valuation_amount
            )
            for item in target.fifo_slices
        ),
        ZERO,
    )

    if (
        total_slice_quantity
        != _decimal(
            target.restored_quantity
        )
    ):
        raise SalesReturnCostRestorationDataIntegrityError(
            "FIFO target slice quantity does not "
            "match parent restoration quantity"
        )

    if (
        total_slice_valuation
        != _decimal(
            target.restored_valuation_amount
        )
    ):
        raise SalesReturnCostRestorationDataIntegrityError(
            "FIFO target slice valuation does not "
            "match parent restoration valuation"
        )

    seen_consumption_ids = set()

    for slice_target in target.fifo_slices:
        if (
            slice_target.fifo_consumption_id
            in seen_consumption_ids
        ):
            raise SalesReturnCostRestorationDataIntegrityError(
                "Duplicate FIFO consumption in target"
            )

        seen_consumption_ids.add(
            slice_target.fifo_consumption_id
        )

        consumption = (
            await db.execute(
                select(
                    StockLotConsumption
                )
                .where(
                    StockLotConsumption.id
                    == (
                        slice_target
                        .fifo_consumption_id
                    )
                )
                .with_for_update()
            )
        ).scalar_one_or_none()

        if consumption is None:
            raise SalesReturnCostRestorationSourceNotFoundError(
                "StockLotConsumption source was not found"
            )

        if consumption.company_id != company_id:
            raise SalesReturnCostRestorationDataIntegrityError(
                "FIFO consumption company mismatch"
            )

        if (
            consumption.issue_document_line_id
            != inventory_cost_entry.document_line_id
        ):
            raise SalesReturnCostRestorationDataIntegrityError(
                "FIFO consumption does not belong to "
                "InventoryCostEntry ISSUE line"
            )

        if (
            consumption.stock_lot_id
            != slice_target.stock_lot_id
        ):
            raise SalesReturnCostRestorationDataIntegrityError(
                "FIFO stock_lot_id provenance mismatch"
            )

        if (
            _decimal(
                consumption.unit_cost
            )
            != _decimal(
                slice_target.unit_cost
            )
        ):
            raise SalesReturnCostRestorationDataIntegrityError(
                "FIFO historical unit cost mismatch"
            )

        if (
            _decimal(
                slice_target.quantity
            )
            > _decimal(
                consumption.quantity
            )
        ):
            raise SalesReturnCostRestorationDataIntegrityError(
                "FIFO restored quantity exceeds "
                "original consumption quantity"
            )


def _event_fifo_slices(
    *,
    event_id: int,
    fifo_slices: Iterable[
        SalesReturnCostRestorationFifoSlice
    ],
) -> tuple[
    SalesReturnCostRestorationFifoSlice,
    ...,
]:
    return tuple(
        sorted(
            (
                item
                for item in fifo_slices
                if (
                    item.sales_return_cost_restoration_event_id
                    == event_id
                )
            ),
            key=lambda item: (
                item.id or 0
            ),
        )
    )


def _add_fifo_children_from_target(
    db: AsyncSession,
    *,
    company_id: int,
    parent_event_id: int,
    target: SalesReturnCostTarget,
) -> None:
    for item in target.fifo_slices:
        db.add(
            SalesReturnCostRestorationFifoSlice(
                company_id=company_id,
                sales_return_cost_restoration_event_id=(
                    parent_event_id
                ),
                fifo_consumption_id=(
                    item.fifo_consumption_id
                ),
                stock_lot_id=(
                    item.stock_lot_id
                ),
                restored_quantity=(
                    item.quantity
                ),
                historical_unit_cost=(
                    item.unit_cost
                ),
                restored_valuation_amount=(
                    item.valuation_amount
                ),
            )
        )


def _add_fifo_children_from_history(
    db: AsyncSession,
    *,
    company_id: int,
    parent_event_id: int,
    source_slices: Iterable[
        SalesReturnCostRestorationFifoSlice
    ],
) -> None:
    for item in source_slices:
        db.add(
            SalesReturnCostRestorationFifoSlice(
                company_id=company_id,
                sales_return_cost_restoration_event_id=(
                    parent_event_id
                ),
                fifo_consumption_id=(
                    item.fifo_consumption_id
                ),
                stock_lot_id=(
                    item.stock_lot_id
                ),
                restored_quantity=(
                    item.restored_quantity
                ),
                historical_unit_cost=(
                    item.historical_unit_cost
                ),
                restored_valuation_amount=(
                    item.restored_valuation_amount
                ),
            )
        )


async def reconcile_sales_return_cost_restoration_source(
    db: AsyncSession,
    *,
    company_id: int,
    target: SalesReturnCostTarget,
    created_by: int,
    reversal_date: date | None = None,
) -> tuple[
    SalesReturnCostRestorationEvent,
    ...,
]:
    """
    Persist immutable state transition for one
    TradeReturnEvent + InventoryCostEntry pair.

    Caller owns COMMIT / ROLLBACK.
    """

    if company_id <= 0:
        raise ValueError(
            "company_id must be greater than zero"
        )

    if created_by <= 0:
        raise ValueError(
            "created_by must be greater than zero"
        )

    if target.return_source_id <= 0:
        raise ValueError(
            "return_source_id must be greater than zero"
        )

    if target.inventory_cost_entry_id <= 0:
        raise ValueError(
            "inventory_cost_entry_id must be greater than zero"
        )

    _validate_zero_target(
        target
    )

    if _target_is_positive(
        target
    ):
        await _validate_positive_target_sources(
            db,
            company_id=company_id,
            target=target,
        )

    events = await _load_pair_history(
        db,
        company_id=company_id,
        trade_return_event_id=(
            target.return_source_id
        ),
        inventory_cost_entry_id=(
            target.inventory_cost_entry_id
        ),
        lock=True,
    )

    fifo_history = (
        await _load_fifo_slices_for_events(
            db,
            event_ids=(
                event.id
                for event in events
                if event.id is not None
            ),
            lock=True,
        )
    )

    plan = (
        build_sales_return_cost_restoration_source_plan(
            events=events,
            fifo_slices=fifo_history,
            target=target,
        )
    )

    if (
        not plan.reversal_event_ids
        and plan.replacement_target is None
    ):
        return ()

    by_id = {
        event.id: event
        for event in events
    }

    created = []

    for original_id in (
        plan.reversal_event_ids
    ):
        original = by_id.get(
            original_id
        )

        if original is None:
            raise SalesReturnCostRestorationDataIntegrityError(
                "Planned reversal event was not found "
                "in locked history"
            )

        effective_reversal_date = (
            reversal_date
            or target.event_date
        )

        reversal_event = (
            SalesReturnCostRestorationEvent(
                company_id=company_id,
                trade_return_event_id=(
                    original.trade_return_event_id
                ),
                inventory_cost_entry_id=(
                    original.inventory_cost_entry_id
                ),
                restoration_date=(
                    effective_reversal_date
                ),
                valuation_method=(
                    original.valuation_method
                ),
                restored_quantity=(
                    original.restored_quantity
                ),
                restored_valuation_amount=(
                    original.restored_valuation_amount
                ),
                restored_cost_amount=(
                    original.restored_cost_amount
                ),
                aggregate_historical_unit_cost=(
                    original.aggregate_historical_unit_cost
                ),
                created_by=created_by,
                reversal_of_id=original.id,
            )
        )

        db.add(
            reversal_event
        )

        await db.flush()

        original_children = (
            _event_fifo_slices(
                event_id=original.id,
                fifo_slices=fifo_history,
            )
        )

        _add_fifo_children_from_history(
            db,
            company_id=company_id,
            parent_event_id=(
                reversal_event.id
            ),
            source_slices=(
                original_children
            ),
        )

        created.append(
            reversal_event
        )

    replacement = (
        plan.replacement_target
    )

    if replacement is not None:
        replacement_event = (
            SalesReturnCostRestorationEvent(
                company_id=company_id,
                trade_return_event_id=(
                    replacement.return_source_id
                ),
                inventory_cost_entry_id=(
                    replacement.inventory_cost_entry_id
                ),
                restoration_date=(
                    replacement.event_date
                ),
                valuation_method=(
                    replacement.valuation_method
                ),
                restored_quantity=(
                    replacement.restored_quantity
                ),
                restored_valuation_amount=(
                    replacement.restored_valuation_amount
                ),
                restored_cost_amount=(
                    replacement.restored_cost_amount
                ),
                aggregate_historical_unit_cost=(
                    replacement.aggregate_historical_unit_cost
                ),
                created_by=created_by,
                reversal_of_id=None,
            )
        )

        db.add(
            replacement_event
        )

        await db.flush()

        _add_fifo_children_from_target(
            db,
            company_id=company_id,
            parent_event_id=(
                replacement_event.id
            ),
            target=replacement,
        )

        created.append(
            replacement_event
        )

    await db.flush()

    return tuple(
        created
    )
