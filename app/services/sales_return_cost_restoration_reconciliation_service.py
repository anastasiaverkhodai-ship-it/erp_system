from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import (
    Document,
)
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
    SalesReturnCostCandidate,
    SalesReturnCostTarget,
    SalesReturnFifoCostSlice,
    SalesReturnIssueCostSource,
    build_sales_return_cost_targets,
)
from app.services.sales_return_cost_restoration_persistence_service import (
    SalesReturnCostRestorationDataIntegrityError,
    SalesReturnCostRestorationPersistenceError,
    build_current_sales_return_cost_restoration_targets,
    reconcile_sales_return_cost_restoration_source,
)


ZERO = Decimal("0")


class SalesReturnCostRestorationReconciliationError(
    Exception
):
    """Base Sales Return cost-restoration reconciliation error."""


class SalesReturnCostRestorationReconciliationSourceError(
    SalesReturnCostRestorationReconciliationError
):
    """Original inventory costing source is missing or invalid."""


@dataclass(
    frozen=True,
    slots=True,
)
class SalesReturnCostRestorationReconciliationResult:
    fulfillment_id: int
    fulfillment_line_id: int
    inventory_cost_entry_id: int

    return_candidates: tuple[
        SalesReturnCostCandidate,
        ...,
    ]

    fifo_source_slices: tuple[
        SalesReturnFifoCostSlice,
        ...,
    ]

    current_targets: tuple[
        SalesReturnCostTarget,
        ...,
    ]

    desired_targets: tuple[
        SalesReturnCostTarget,
        ...,
    ]

    reconciliation_targets: tuple[
        SalesReturnCostTarget,
        ...,
    ]

    created_events: tuple[
        SalesReturnCostRestorationEvent,
        ...,
    ]


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


def _zero_target_from_current(
    current: SalesReturnCostTarget,
) -> SalesReturnCostTarget:
    return SalesReturnCostTarget(
        return_source_id=(
            current.return_source_id
        ),
        inventory_cost_entry_id=(
            current.inventory_cost_entry_id
        ),
        event_date=(
            current.event_date
        ),
        valuation_method=(
            current.valuation_method
        ),
        restored_quantity=Decimal(
            "0"
        ),
        restored_valuation_amount=Decimal(
            "0"
        ),
        restored_cost_amount=Decimal(
            "0"
        ),
        aggregate_historical_unit_cost=Decimal(
            "0"
        ),
        fifo_slices=(),
    )


def build_sales_return_cost_restoration_reconciliation_targets(
    *,
    desired_targets: Iterable[
        SalesReturnCostTarget
    ],
    current_targets: Iterable[
        SalesReturnCostTarget
    ],
) -> tuple[
    SalesReturnCostTarget,
    ...,
]:
    desired = {
        target.pair_key: target
        for target
        in desired_targets
    }

    current = {
        target.pair_key: target
        for target
        in current_targets
    }

    if len(
        desired
    ) != len(
        tuple(
            desired_targets
        )
    ):
        raise SalesReturnCostRestorationReconciliationError(
            "Duplicate desired cost-restoration pair"
        )

    if len(
        current
    ) != len(
        tuple(
            current_targets
        )
    ):
        raise SalesReturnCostRestorationReconciliationError(
            "Duplicate current cost-restoration pair"
        )

    actions = []

    all_pairs = (
        set(
            desired
        )
        | set(
            current
        )
    )

    for pair in all_pairs:
        desired_target = desired.get(
            pair
        )

        current_target = current.get(
            pair
        )

        if (
            desired_target is not None
            and current_target is not None
            and desired_target == current_target
        ):
            continue

        if desired_target is None:
            actions.append(
                (
                    0,
                    _zero_target_from_current(
                        current_target
                    ),
                )
            )

            continue

        if current_target is not None:
            actions.append(
                (
                    0,
                    desired_target,
                )
            )

            continue

        actions.append(
            (
                1,
                desired_target,
            )
        )

    actions.sort(
        key=lambda item: (
            item[0],
            item[1].event_date,
            item[1].return_source_id,
            item[1].inventory_cost_entry_id,
        )
    )

    return tuple(
        target
        for _priority, target
        in actions
    )


async def _load_source_context(
    db: AsyncSession,
    *,
    company_id: int,
    fulfillment_id: int,
    fulfillment_line_id: int,
) -> tuple[
    TradeFulfillmentLine,
    InventoryCostEntry,
    SalesReturnIssueCostSource,
    tuple[
        SalesReturnFifoCostSlice,
        ...,
    ],
]:
    fulfillment_line = (
        await db.execute(
            select(
                TradeFulfillmentLine
            )
            .where(
                TradeFulfillmentLine.company_id
                == company_id,
                TradeFulfillmentLine.fulfillment_id
                == fulfillment_id,
                TradeFulfillmentLine.id
                == fulfillment_line_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()

    if fulfillment_line is None:
        raise SalesReturnCostRestorationReconciliationSourceError(
            "Original TradeFulfillmentLine "
            "was not found"
        )

    if (
        fulfillment_line.warehouse_document_line_id
        is None
    ):
        raise SalesReturnCostRestorationReconciliationSourceError(
            "Original fulfillment line has no "
            "warehouse ISSUE line"
        )

    cost_entry = (
        await db.execute(
            select(
                InventoryCostEntry
            )
            .where(
                InventoryCostEntry.document_line_id
                == (
                    fulfillment_line
                    .warehouse_document_line_id
                )
            )
            .with_for_update()
        )
    ).scalar_one_or_none()

    if cost_entry is None:
        raise SalesReturnCostRestorationReconciliationSourceError(
            "Original InventoryCostEntry "
            "was not found"
        )

    if cost_entry.company_id != company_id:
        raise SalesReturnCostRestorationReconciliationSourceError(
            "InventoryCostEntry company mismatch"
        )

    issue_document = (
        await db.execute(
            select(
                Document
            ).where(
                Document.id
                == cost_entry.document_id,
                Document.company_id
                == company_id,
            )
        )
    ).scalar_one_or_none()

    if issue_document is None:
        raise SalesReturnCostRestorationReconciliationSourceError(
            "Original warehouse ISSUE document "
            "was not found"
        )

    document_type = _method_value(
        issue_document.document_type
    )

    if document_type != "issue":
        raise SalesReturnCostRestorationReconciliationSourceError(
            "InventoryCostEntry source document "
            "is not an ISSUE"
        )

    method = _method_value(
        cost_entry.valuation_method
    )

    source = SalesReturnIssueCostSource(
        source_id=cost_entry.id,
        issue_date=(
            issue_document.document_date
        ),
        valuation_method=method,
        quantity=_decimal(
            cost_entry.quantity
        ),
        unit_cost=_decimal(
            cost_entry.unit_cost
        ),
        valuation_amount=_decimal(
            cost_entry.valuation_amount
        ),
        cost_amount=_decimal(
            cost_entry.cost_amount
        ),
    )

    fifo_slices = ()

    if method == FIFO:
        consumptions = tuple(
            (
                await db.scalars(
                    select(
                        StockLotConsumption
                    )
                    .where(
                        StockLotConsumption.company_id
                        == company_id,
                        (
                            StockLotConsumption
                            .issue_document_line_id
                            == cost_entry.document_line_id
                        ),
                    )
                    .order_by(
                        StockLotConsumption.id
                    )
                    .with_for_update()
                )
            ).all()
        )

        fifo_slices = tuple(
            SalesReturnFifoCostSlice(
                source_id=item.id,
                stock_lot_id=(
                    item.stock_lot_id
                ),
                quantity=_decimal(
                    item.quantity
                ),
                unit_cost=_decimal(
                    item.unit_cost
                ),
            )
            for item in consumptions
        )

    return (
        fulfillment_line,
        cost_entry,
        source,
        fifo_slices,
    )


async def _load_active_return_candidates(
    db: AsyncSession,
    *,
    company_id: int,
    fulfillment_id: int,
    fulfillment_line_id: int,
    product_id: int,
) -> tuple[
    SalesReturnCostCandidate,
    ...,
]:
    events = tuple(
        (
            await db.scalars(
                select(
                    TradeReturnEvent
                )
                .where(
                    TradeReturnEvent.company_id
                    == company_id,
                    (
                        TradeReturnEvent
                        .original_fulfillment_id
                        == fulfillment_id
                    ),
                    (
                        TradeReturnEvent
                        .original_fulfillment_line_id
                        == fulfillment_line_id
                    ),
                )
                .order_by(
                    TradeReturnEvent.id
                )
                .with_for_update()
            )
        ).all()
    )

    by_id = {
        event.id: event
        for event in events
    }

    reversed_ids = set()

    for event in events:
        if event.reversal_of_id is None:
            continue

        original = by_id.get(
            event.reversal_of_id
        )

        if original is None:
            raise SalesReturnCostRestorationReconciliationError(
                "Trade Return reversal references "
                "an unloaded source event"
            )

        reversed_ids.add(
            event.reversal_of_id
        )

    active = []

    for event in events:
        if event.reversal_of_id is not None:
            continue

        if event.id in reversed_ids:
            continue

        direction = _method_value(
            event.direction
        )

        if direction != "sale":
            continue

        if event.product_id != product_id:
            raise SalesReturnCostRestorationReconciliationError(
                "Active Trade Return product differs "
                "from original fulfillment product"
            )

        active.append(
            SalesReturnCostCandidate(
                return_source_id=event.id,
                event_date=(
                    event.return_date
                ),
                quantity=_decimal(
                    event.returned_quantity
                ),
            )
        )

    return tuple(
        sorted(
            active,
            key=lambda candidate: (
                candidate.event_date,
                candidate.return_source_id,
            ),
        )
    )


async def _load_current_targets(
    db: AsyncSession,
    *,
    company_id: int,
    inventory_cost_entry_id: int,
) -> tuple[
    SalesReturnCostTarget,
    ...,
]:
    events = tuple(
        (
            await db.scalars(
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
                        .inventory_cost_entry_id
                        == inventory_cost_entry_id
                    ),
                )
                .order_by(
                    SalesReturnCostRestorationEvent.id
                )
                .with_for_update()
            )
        ).all()
    )

    event_ids = tuple(
        event.id
        for event in events
        if event.id is not None
    )

    if event_ids:
        fifo_slices = tuple(
            (
                await db.scalars(
                    select(
                        SalesReturnCostRestorationFifoSlice
                    )
                    .where(
                        (
                            SalesReturnCostRestorationFifoSlice
                            .sales_return_cost_restoration_event_id
                            .in_(
                                event_ids
                            )
                        )
                    )
                    .order_by(
                        (
                            SalesReturnCostRestorationFifoSlice
                            .sales_return_cost_restoration_event_id
                        ),
                        (
                            SalesReturnCostRestorationFifoSlice
                            .id
                        ),
                    )
                    .with_for_update()
                )
            ).all()
        )
    else:
        fifo_slices = ()

    try:
        return (
            build_current_sales_return_cost_restoration_targets(
                events=events,
                fifo_slices=fifo_slices,
            )
        )
    except (
        SalesReturnCostRestorationDataIntegrityError
    ) as exc:
        raise SalesReturnCostRestorationReconciliationError(
            str(
                exc
            )
        ) from exc


async def reconcile_sales_return_cost_restoration_for_fulfillment_line(
    db: AsyncSession,
    *,
    company_id: int,
    fulfillment_id: int,
    fulfillment_line_id: int,
    created_by: int,
    adjustment_date: date | None = None,
) -> SalesReturnCostRestorationReconciliationResult:
    """
    Reconcile desired historical COGS-restoration state for one
    original sales fulfillment line.

    No stock mutation.
    No JournalEntry.
    No COMMIT / ROLLBACK.
    """

    if company_id <= 0:
        raise ValueError(
            "company_id must be greater than zero"
        )

    if fulfillment_id <= 0:
        raise ValueError(
            "fulfillment_id must be greater than zero"
        )

    if fulfillment_line_id <= 0:
        raise ValueError(
            "fulfillment_line_id must be greater than zero"
        )

    if created_by <= 0:
        raise ValueError(
            "created_by must be greater than zero"
        )

    (
        fulfillment_line,
        cost_entry,
        source,
        fifo_source_slices,
    ) = await _load_source_context(
        db,
        company_id=company_id,
        fulfillment_id=fulfillment_id,
        fulfillment_line_id=(
            fulfillment_line_id
        ),
    )

    return_candidates = (
        await _load_active_return_candidates(
            db,
            company_id=company_id,
            fulfillment_id=fulfillment_id,
            fulfillment_line_id=(
                fulfillment_line_id
            ),
            product_id=(
                fulfillment_line.product_id
            ),
        )
    )

    desired_targets = (
        build_sales_return_cost_targets(
            source=source,
            candidates=return_candidates,
            fifo_slices=fifo_source_slices,
        )
    )

    current_targets = (
        await _load_current_targets(
            db,
            company_id=company_id,
            inventory_cost_entry_id=(
                cost_entry.id
            ),
        )
    )

    reconciliation_targets = (
        build_sales_return_cost_restoration_reconciliation_targets(
            desired_targets=desired_targets,
            current_targets=current_targets,
        )
    )

    created_events = []

    for target in reconciliation_targets:
        try:
            created_events.extend(
                await reconcile_sales_return_cost_restoration_source(
                    db,
                    company_id=company_id,
                    target=target,
                    created_by=created_by,
                    reversal_date=(
                        adjustment_date
                    ),
                )
            )
        except (
            SalesReturnCostRestorationPersistenceError
        ) as exc:
            raise SalesReturnCostRestorationReconciliationError(
                str(
                    exc
                )
            ) from exc

    return (
        SalesReturnCostRestorationReconciliationResult(
            fulfillment_id=fulfillment_id,
            fulfillment_line_id=(
                fulfillment_line_id
            ),
            inventory_cost_entry_id=(
                cost_entry.id
            ),
            return_candidates=(
                return_candidates
            ),
            fifo_source_slices=(
                fifo_source_slices
            ),
            current_targets=(
                current_targets
            ),
            desired_targets=(
                desired_targets
            ),
            reconciliation_targets=(
                reconciliation_targets
            ),
            created_events=tuple(
                created_events
            ),
        )
    )
