from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document_line import DocumentLine
from app.models.inventory_cost_entry import InventoryCostEntry
from app.models.purchase_value_correction_moving_average_replay_event import (
    PurchaseValueCorrectionMovingAverageReplayEvent,
)
from app.models.sales_return_cost_restoration_event import (
    SalesReturnCostRestorationEvent,
)


ZERO = Decimal("0")


class PurchaseValueCorrectionMovingAverageSalesReturnSourceError(
    Exception
):
    """Invalid PVC MA / Sales Return immutable source history."""


@dataclass(
    frozen=True,
    slots=True,
)
class PurchaseValueCorrectionMovingAverageSalesReturnCandidate:
    cost_restoration_event_id: int
    trade_return_event_id: int
    restoration_date: date
    restored_quantity: Decimal


@dataclass(
    frozen=True,
    slots=True,
)
class PurchaseValueCorrectionMovingAverageSalesReturnIssuedSource:
    allocation_event_id: int
    replay_event_id: int
    source_moving_average_movement_id: int
    source_inventory_cost_entry_id: int
    product_id: int
    warehouse_id: int
    quantity: Decimal
    original_valuation_amount: Decimal
    corrected_valuation_amount: Decimal
    currency_code: str

    @property
    def valuation_delta(
        self,
    ) -> Decimal:
        return (
            self.corrected_valuation_amount
            - self.original_valuation_amount
        )


@dataclass(
    frozen=True,
    slots=True,
)
class PurchaseValueCorrectionMovingAverageSalesReturnSource:
    cost_restoration_event_id: int
    inventory_cost_entry_id: int
    product_id: int
    warehouse_id: int
    historical_allocation_event_ids: tuple[int, ...]
    active_return_candidates: tuple[
        PurchaseValueCorrectionMovingAverageSalesReturnCandidate,
        ...,
    ]
    issued_sources: tuple[
        PurchaseValueCorrectionMovingAverageSalesReturnIssuedSource,
        ...,
    ]

    @property
    def total_restored_quantity(
        self,
    ) -> Decimal:
        return sum(
            (
                item.restored_quantity
                for item in self.active_return_candidates
            ),
            ZERO,
        )


def _decimal(
    value,
) -> Decimal:
    return Decimal(
        value
    )


def _active_originals(
    events,
):
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
            raise (
                PurchaseValueCorrectionMovingAverageSalesReturnSourceError(
                    "PVC MA reversal references "
                    "an unloaded source event"
                )
            )

        if original.reversal_of_id is not None:
            raise (
                PurchaseValueCorrectionMovingAverageSalesReturnSourceError(
                    "PVC MA reversal cannot reverse "
                    "another reversal"
                )
            )

        reversed_ids.add(
            original.id
        )

    return tuple(
        event
        for event in events
        if event.reversal_of_id is None
        and event.id not in reversed_ids
    )


async def load_purchase_value_correction_moving_average_sales_return_source(
    db: AsyncSession,
    *,
    company_id: int,
    sales_return_cost_restoration_event_id: int,
) -> PurchaseValueCorrectionMovingAverageSalesReturnSource:
    """
    Load immutable PVC MA issued impacts affected by one ACTIVE
    weighted-average Sales Return cost-restoration event.

    Provenance:

        SalesReturnCostRestorationEvent
            -> original InventoryCostEntry
            -> ACTIVE PVC MA issued replay impacts
               having the same source_inventory_cost_entry_id

    No MovingAverageBalance mutation.
    No MovingAverageMovement mutation.
    No InventoryCostEntry mutation.
    No JournalEntry.
    No COMMIT / ROLLBACK.
    """

    if company_id <= 0:
        raise ValueError(
            "company_id must be greater than zero"
        )

    if sales_return_cost_restoration_event_id <= 0:
        raise ValueError(
            "sales_return_cost_restoration_event_id "
            "must be greater than zero"
        )

    cost_event = (
        await db.execute(
            select(
                SalesReturnCostRestorationEvent
            )
            .where(
                SalesReturnCostRestorationEvent.company_id
                == company_id,
                SalesReturnCostRestorationEvent.id
                == sales_return_cost_restoration_event_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()

    if cost_event is None:
        raise (
            PurchaseValueCorrectionMovingAverageSalesReturnSourceError(
                "Sales Return cost-restoration event "
                "was not found"
            )
        )

    if cost_event.reversal_of_id is not None:
        raise (
            PurchaseValueCorrectionMovingAverageSalesReturnSourceError(
                "Sales Return source must be "
                "an original/replacement event"
            )
        )

    if str(
        getattr(
            cost_event.valuation_method,
            "value",
            cost_event.valuation_method,
        )
    ) != "weighted_average_moving":
        raise (
            PurchaseValueCorrectionMovingAverageSalesReturnSourceError(
                "Sales Return source is not "
                "weighted_average_moving"
            )
        )

    # The cost-restoration original itself must still be active.
    cost_history = tuple(
        (
            await db.scalars(
                select(
                    SalesReturnCostRestorationEvent
                )
                .where(
                    SalesReturnCostRestorationEvent.company_id
                    == company_id,
                    SalesReturnCostRestorationEvent.inventory_cost_entry_id
                    == cost_event.inventory_cost_entry_id,
                )
                .order_by(
                    SalesReturnCostRestorationEvent.id
                )
                .with_for_update()
            )
        ).all()
    )

    cost_by_id = {
        item.id: item
        for item in cost_history
    }

    reversed_cost_ids = set()

    for item in cost_history:
        if item.reversal_of_id is None:
            continue

        original = cost_by_id.get(
            item.reversal_of_id
        )

        if original is None:
            raise (
                PurchaseValueCorrectionMovingAverageSalesReturnSourceError(
                    "Sales Return cost reversal references "
                    "an unloaded source event"
                )
            )

        if original.reversal_of_id is not None:
            raise (
                PurchaseValueCorrectionMovingAverageSalesReturnSourceError(
                    "Sales Return cost reversal cannot "
                    "reverse another reversal"
                )
            )

        if item.reversal_of_id in reversed_cost_ids:
            raise (
                PurchaseValueCorrectionMovingAverageSalesReturnSourceError(
                    "Sales Return cost original has "
                    "multiple reversals"
                )
            )

        reversed_cost_ids.add(
            item.reversal_of_id
        )

    # Historical original-side Sales Return cost events remain valid
    # PVC provenance anchors after a later immutable reversal.
    # Active return candidates are resolved independently through
    # the original -> reversal graph below.

    active_cost_events = tuple(
        item
        for item in cost_history
        if item.reversal_of_id is None
        and item.id not in reversed_cost_ids
    )

    active_return_candidates = []

    for item in active_cost_events:
        method = str(
            getattr(
                item.valuation_method,
                "value",
                item.valuation_method,
            )
        )

        if method != "weighted_average_moving":
            raise (
                PurchaseValueCorrectionMovingAverageSalesReturnSourceError(
                    "Active Sales Return cost history "
                    "contains a non-moving-average event"
                )
            )

        quantity = _decimal(
            item.restored_quantity
        )

        if quantity <= ZERO:
            raise (
                PurchaseValueCorrectionMovingAverageSalesReturnSourceError(
                    "Active Sales Return restored quantity "
                    "must be positive"
                )
            )

        active_return_candidates.append(
            PurchaseValueCorrectionMovingAverageSalesReturnCandidate(
                cost_restoration_event_id=item.id,
                trade_return_event_id=(
                    item.trade_return_event_id
                ),
                restoration_date=(
                    item.restoration_date
                ),
                restored_quantity=quantity,
            )
        )

    active_return_candidates = tuple(
        sorted(
            active_return_candidates,
            key=lambda item: (
                item.restoration_date,
                item.cost_restoration_event_id,
            ),
        )
    )

    issue_document_line = (
        await db.execute(
            select(
                DocumentLine
            )
            .join(
                InventoryCostEntry,
                InventoryCostEntry.document_line_id
                == DocumentLine.id,
            )
            .where(
                InventoryCostEntry.id
                == cost_event.inventory_cost_entry_id,
                InventoryCostEntry.company_id
                == company_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()

    if issue_document_line is None:
        raise (
            PurchaseValueCorrectionMovingAverageSalesReturnSourceError(
                "Sales Return historical InventoryCostEntry "
                "does not resolve to its exact DocumentLine"
            )
        )

    if (
        issue_document_line.product_id is None
        or issue_document_line.product_id <= 0
    ):
        raise (
            PurchaseValueCorrectionMovingAverageSalesReturnSourceError(
                "Historical ISSUE DocumentLine has invalid product_id"
            )
        )

    if (
        issue_document_line.warehouse_id is None
        or issue_document_line.warehouse_id <= 0
    ):
        raise (
            PurchaseValueCorrectionMovingAverageSalesReturnSourceError(
                "Historical ISSUE DocumentLine has invalid warehouse_id"
            )
        )

    replay_history = tuple(
        (
            await db.scalars(
                select(
                    PurchaseValueCorrectionMovingAverageReplayEvent
                )
                .where(
                    PurchaseValueCorrectionMovingAverageReplayEvent.company_id
                    == company_id,
                    PurchaseValueCorrectionMovingAverageReplayEvent.source_inventory_cost_entry_id
                    == cost_event.inventory_cost_entry_id,
                )
                .order_by(
                    PurchaseValueCorrectionMovingAverageReplayEvent.id
                )
                .with_for_update()
            )
        ).all()
    )

    active = _active_originals(
        replay_history
    )

    issued = tuple(
        event
        for event in active
        if event.effect_kind == "issued"
    )

    sources = []

    for event in issued:
        if (
            event.source_moving_average_movement_id
            is None
            or event.source_inventory_cost_entry_id
            is None
        ):
            raise (
                PurchaseValueCorrectionMovingAverageSalesReturnSourceError(
                    "Active issued PVC MA event "
                    "has incomplete ISSUE provenance"
                )
            )

        quantity = _decimal(
            event.quantity
        )

        original_value = _decimal(
            event.original_valuation_amount
        )

        corrected_value = _decimal(
            event.corrected_valuation_amount
        )

        if quantity <= ZERO:
            raise (
                PurchaseValueCorrectionMovingAverageSalesReturnSourceError(
                    "Active issued PVC MA quantity "
                    "must be positive"
                )
            )

        if original_value == corrected_value:
            raise (
                PurchaseValueCorrectionMovingAverageSalesReturnSourceError(
                    "Active issued PVC MA event "
                    "cannot be a no-op"
                )
            )

        sources.append(
            PurchaseValueCorrectionMovingAverageSalesReturnIssuedSource(
                allocation_event_id=(
                    event.purchase_value_correction_allocation_event_id
                ),
                replay_event_id=event.id,
                source_moving_average_movement_id=(
                    event.source_moving_average_movement_id
                ),
                source_inventory_cost_entry_id=(
                    event.source_inventory_cost_entry_id
                ),
                product_id=event.product_id,
                warehouse_id=event.warehouse_id,
                quantity=quantity,
                original_valuation_amount=(
                    original_value
                ),
                corrected_valuation_amount=(
                    corrected_value
                ),
                currency_code=event.currency_code,
            )
        )

    return (
        PurchaseValueCorrectionMovingAverageSalesReturnSource(
            cost_restoration_event_id=cost_event.id,
            inventory_cost_entry_id=(
                cost_event.inventory_cost_entry_id
            ),
            product_id=issue_document_line.product_id,
            warehouse_id=issue_document_line.warehouse_id,
            historical_allocation_event_ids=tuple(
                sorted(
                    {
                        event.purchase_value_correction_allocation_event_id
                        for event in replay_history
                        if event.effect_kind == "issued"
                        and event.source_inventory_cost_entry_id
                        == cost_event.inventory_cost_entry_id
                    }
                )
            ),
            active_return_candidates=(
                active_return_candidates
            ),
            issued_sources=tuple(
                sorted(
                    sources,
                    key=lambda item: (
                        item.allocation_event_id,
                        item.replay_event_id,
                    ),
                )
            ),
        )
    )
