from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.purchase_value_correction_moving_average_replay_calculation_service import (
    MovingAverageReplayImpact,
    MovingAverageReplayMovement,
    PurchaseValueCorrectionMovingAverageReplayResult,
)
from app.services.purchase_value_correction_moving_average_replay_source_loader import (
    PurchaseValueCorrectionMovingAverageReplaySource,
)
from app.services.purchase_value_correction_moving_average_transfer_destination_replay_service import (
    PurchaseValueCorrectionMovingAverageTransferDestinationReplay,
    load_and_calculate_moving_average_transfer_destination_replay,
)
from app.services.purchase_value_correction_moving_average_transfer_routing_service import (
    resolve_moving_average_transfer_route,
)


ZERO = Decimal("0")


class PurchaseValueCorrectionMovingAverageTransferCompositionError(
    Exception
):
    """Base MA transfer replay composition error."""


class PurchaseValueCorrectionMovingAverageTransferCompositionIntegrityError(
    PurchaseValueCorrectionMovingAverageTransferCompositionError
):
    """Replay composition/provenance is inconsistent."""


@dataclass(
    frozen=True,
    slots=True,
)
class PurchaseValueCorrectionMovingAverageComposedImpact:
    """
    Final economic PVC MA destination after resolving zero or more
    warehouse-transfer routing boundaries.

    Unlike MovingAverageReplayImpact, this DTO explicitly owns:
      * destination warehouse;
      * primary recognition date;
      * transfer route provenance.

    That prevents reconciliation from incorrectly forcing all
    routed effects back into the original source warehouse.
    """

    company_id: int
    product_id: int
    warehouse_id: int

    effect_kind: str
    recognition_date: date

    quantity: Decimal
    original_valuation_amount: Decimal
    corrected_valuation_amount: Decimal

    source_moving_average_movement_id: int | None
    source_inventory_cost_entry_id: int | None

    warehouse_transfer_valuation_layer_ids: tuple[
        int,
        ...,
    ]

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
class PurchaseValueCorrectionMovingAverageTransferCompositionResult:
    """
    Cross-warehouse economic replay result.

    source_replay_result remains the authoritative replay result for
    the original source MA stream.

    impacts contains final economic destinations after replacing
    transfer ISSUE boundaries with destination MA replay impacts.
    """

    source_replay_result: (
        PurchaseValueCorrectionMovingAverageReplayResult
    )

    impacts: tuple[
        PurchaseValueCorrectionMovingAverageComposedImpact,
        ...,
    ]

    @property
    def impact_delta_total(
        self,
    ) -> Decimal:
        return sum(
            (
                impact.valuation_delta
                for impact in self.impacts
            ),
            ZERO,
        )


def _positive_id(
    value,
    *,
    field: str,
) -> int:
    if (
        not isinstance(
            value,
            int,
        )
        or isinstance(
            value,
            bool,
        )
        or value <= 0
    ):
        raise (
            PurchaseValueCorrectionMovingAverageTransferCompositionIntegrityError(
                f"{field} must be a positive integer"
            )
        )

    return value


def _movement_date_for_impact(
    *,
    impact: MovingAverageReplayImpact,
    later_movements: tuple[
        MovingAverageReplayMovement,
        ...,
    ],
    base_recognition_date: date,
) -> date:
    if impact.effect_kind == "on_hand":
        return base_recognition_date

    if impact.effect_kind != "issued":
        raise (
            PurchaseValueCorrectionMovingAverageTransferCompositionIntegrityError(
                "Unsupported MA replay effect kind"
            )
        )

    movement_id = (
        impact.source_moving_average_movement_id
    )

    if movement_id is None:
        raise (
            PurchaseValueCorrectionMovingAverageTransferCompositionIntegrityError(
                "Issued MA impact requires source movement ID"
            )
        )

    matches = tuple(
        movement
        for movement in later_movements
        if movement.movement_id == movement_id
    )

    if len(matches) != 1:
        raise (
            PurchaseValueCorrectionMovingAverageTransferCompositionIntegrityError(
                "Issued MA impact does not resolve to exactly "
                "one replay movement"
            )
        )

    return max(
        base_recognition_date,
        matches[0].movement_date,
    )


def _final_impact(
    *,
    company_id: int,
    product_id: int,
    warehouse_id: int,
    impact: MovingAverageReplayImpact,
    later_movements: tuple[
        MovingAverageReplayMovement,
        ...,
    ],
    base_recognition_date: date,
    transfer_layer_ids: tuple[
        int,
        ...,
    ],
) -> PurchaseValueCorrectionMovingAverageComposedImpact:
    return (
        PurchaseValueCorrectionMovingAverageComposedImpact(
            company_id=company_id,
            product_id=product_id,
            warehouse_id=warehouse_id,
            effect_kind=impact.effect_kind,
            recognition_date=(
                _movement_date_for_impact(
                    impact=impact,
                    later_movements=later_movements,
                    base_recognition_date=(
                        base_recognition_date
                    ),
                )
            ),
            quantity=impact.quantity,
            original_valuation_amount=(
                impact.original_valuation_amount
            ),
            corrected_valuation_amount=(
                impact.corrected_valuation_amount
            ),
            source_moving_average_movement_id=(
                impact
                .source_moving_average_movement_id
            ),
            source_inventory_cost_entry_id=(
                impact
                .source_inventory_cost_entry_id
            ),
            warehouse_transfer_valuation_layer_ids=(
                transfer_layer_ids
            ),
        )
    )


async def _expand_impact(
    db: AsyncSession,
    *,
    company_id: int,
    product_id: int,
    warehouse_id: int,
    impact: MovingAverageReplayImpact,
    later_movements: tuple[
        MovingAverageReplayMovement,
        ...,
    ],
    base_recognition_date: date,
    transfer_layer_ids: tuple[
        int,
        ...,
    ],
    visited_source_inventory_cost_entry_ids: frozenset[
        int
    ],
) -> tuple[
    PurchaseValueCorrectionMovingAverageComposedImpact,
    ...,
]:
    """
    Recursively replace transfer ISSUE impacts with destination
    replay impacts.

    A non-transfer ISSUE remains an economic issued destination.

    A transfer ISSUE is only a routing boundary:
        source ICE
          -> WTVL
          -> destination receipt
          -> destination replay
          -> recursively resolve later transfer ISSUE if required.
    """

    if impact.effect_kind == "on_hand":
        return (
            _final_impact(
                company_id=company_id,
                product_id=product_id,
                warehouse_id=warehouse_id,
                impact=impact,
                later_movements=later_movements,
                base_recognition_date=(
                    base_recognition_date
                ),
                transfer_layer_ids=(
                    transfer_layer_ids
                ),
            ),
        )

    if impact.effect_kind != "issued":
        raise (
            PurchaseValueCorrectionMovingAverageTransferCompositionIntegrityError(
                "Unsupported MA replay effect kind"
            )
        )

    source_ice_id = (
        impact.source_inventory_cost_entry_id
    )

    if source_ice_id is None:
        raise (
            PurchaseValueCorrectionMovingAverageTransferCompositionIntegrityError(
                "Issued MA impact requires InventoryCostEntry ID"
            )
        )

    source_ice_id = _positive_id(
        source_ice_id,
        field="source InventoryCostEntry id",
    )

    if (
        source_ice_id
        in visited_source_inventory_cost_entry_ids
    ):
        raise (
            PurchaseValueCorrectionMovingAverageTransferCompositionIntegrityError(
                "Warehouse-transfer MA routing cycle detected"
            )
        )

    route = (
        await resolve_moving_average_transfer_route(
            db,
            company_id=company_id,
            source_inventory_cost_entry_id=(
                source_ice_id
            ),
        )
    )

    if route is None:
        return (
            _final_impact(
                company_id=company_id,
                product_id=product_id,
                warehouse_id=warehouse_id,
                impact=impact,
                later_movements=later_movements,
                base_recognition_date=(
                    base_recognition_date
                ),
                transfer_layer_ids=(
                    transfer_layer_ids
                ),
            ),
        )

    if (
        route.company_id != company_id
        or route.product_id != product_id
        or route.source_inventory_cost_entry_id
        != source_ice_id
    ):
        raise (
            PurchaseValueCorrectionMovingAverageTransferCompositionIntegrityError(
                "Resolved MA transfer route does not match "
                "source replay impact provenance"
            )
        )

    route_layer_id = _positive_id(
        route.warehouse_transfer_valuation_layer_id,
        field="WTVL id",
    )

    if route_layer_id in transfer_layer_ids:
        raise (
            PurchaseValueCorrectionMovingAverageTransferCompositionIntegrityError(
                "Duplicate WTVL detected in MA transfer path"
            )
        )

    destination = (
        await load_and_calculate_moving_average_transfer_destination_replay(
            db,
            route=route,
            source_transfer_original_valuation_amount=(
                impact.original_valuation_amount
            ),
            source_transfer_corrected_valuation_amount=(
                impact.corrected_valuation_amount
            ),
        )
    )

    if (
        destination.replay_result.receipt_value_delta
        != impact.valuation_delta
    ):
        raise (
            PurchaseValueCorrectionMovingAverageTransferCompositionIntegrityError(
                "Destination MA replay does not conserve "
                "routed transfer valuation delta"
            )
        )

    if (
        destination.replay_result.impact_delta_total
        != impact.valuation_delta
    ):
        raise (
            PurchaseValueCorrectionMovingAverageTransferCompositionIntegrityError(
                "Destination MA replay impact total does not "
                "match routed transfer valuation delta"
            )
        )

    next_transfer_path = (
        transfer_layer_ids
        + (
            route_layer_id,
        )
    )

    next_visited = (
        visited_source_inventory_cost_entry_ids
        | frozenset(
            (
                source_ice_id,
            )
        )
    )

    expanded = []

    for destination_impact in (
        destination.replay_result.impacts
    ):
        expanded.extend(
            await _expand_impact(
                db,
                company_id=company_id,
                product_id=product_id,
                warehouse_id=(
                    route.destination_warehouse_id
                ),
                impact=destination_impact,
                later_movements=(
                    destination
                    .source
                    .later_movements
                ),
                base_recognition_date=(
                    base_recognition_date
                ),
                transfer_layer_ids=(
                    next_transfer_path
                ),
                visited_source_inventory_cost_entry_ids=(
                    next_visited
                ),
            )
        )

    expanded_tuple = tuple(
        expanded
    )

    routed_total = sum(
        (
            item.valuation_delta
            for item in expanded_tuple
        ),
        ZERO,
    )

    if routed_total != impact.valuation_delta:
        raise (
            PurchaseValueCorrectionMovingAverageTransferCompositionIntegrityError(
                "Expanded destination MA impacts do not "
                "conserve source transfer ISSUE delta"
            )
        )

    return expanded_tuple


async def compose_purchase_value_correction_moving_average_transfer_replay(
    db: AsyncSession,
    *,
    source: PurchaseValueCorrectionMovingAverageReplaySource,
    source_replay_result: (
        PurchaseValueCorrectionMovingAverageReplayResult
    ),
) -> (
    PurchaseValueCorrectionMovingAverageTransferCompositionResult
):
    """
    Resolve final economic PVC MA impacts across warehouse transfers.

    No historical row mutation.
    No JournalEntry creation.
    No COMMIT / ROLLBACK.
    """

    company_id = _positive_id(
        source.company_id,
        field="source.company_id",
    )

    product_id = _positive_id(
        source.product_id,
        field="source.product_id",
    )

    warehouse_id = _positive_id(
        source.warehouse_id,
        field="source.warehouse_id",
    )

    if not isinstance(
        source.recognition_date,
        date,
    ):
        raise (
            PurchaseValueCorrectionMovingAverageTransferCompositionIntegrityError(
                "source.recognition_date must be a date"
            )
        )

    if (
        source_replay_result.impact_delta_total
        != source_replay_result.receipt_value_delta
    ):
        raise (
            PurchaseValueCorrectionMovingAverageTransferCompositionIntegrityError(
                "Source MA replay result violates valuation conservation"
            )
        )

    composed = []

    for impact in source_replay_result.impacts:
        composed.extend(
            await _expand_impact(
                db,
                company_id=company_id,
                product_id=product_id,
                warehouse_id=warehouse_id,
                impact=impact,
                later_movements=(
                    source.later_movements
                ),
                base_recognition_date=(
                    source.recognition_date
                ),
                transfer_layer_ids=(),
                visited_source_inventory_cost_entry_ids=(
                    frozenset()
                ),
            )
        )

    impacts = tuple(
        composed
    )

    composed_total = sum(
        (
            impact.valuation_delta
            for impact in impacts
        ),
        ZERO,
    )

    if (
        composed_total
        != source_replay_result.receipt_value_delta
    ):
        raise (
            PurchaseValueCorrectionMovingAverageTransferCompositionIntegrityError(
                "Cross-warehouse MA composition does not "
                "conserve original PVC receipt valuation delta"
            )
        )

    return (
        PurchaseValueCorrectionMovingAverageTransferCompositionResult(
            source_replay_result=(
                source_replay_result
            ),
            impacts=impacts,
        )
    )
