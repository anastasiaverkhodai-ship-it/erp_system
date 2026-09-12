from dataclasses import dataclass, replace
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.purchase_value_correction_moving_average_replay_calculation_service import (
    PurchaseValueCorrectionMovingAverageReplayResult,
)

from app.services.purchase_value_correction_moving_average_transfer_composition_service import (
    PurchaseValueCorrectionMovingAverageComposedImpact,
    compose_purchase_value_correction_moving_average_transfer_replay,
)

from app.models.purchase_value_correction_moving_average_replay_event import (
    PurchaseValueCorrectionMovingAverageReplayEvent,
)
from app.services.purchase_value_correction_moving_average_peer_replay_service import (
    MovingAveragePeerAttributedImpact,
    PurchaseValueCorrectionMovingAveragePeerReplayResult,
    calculate_purchase_value_correction_moving_average_peer_replay,
)
from app.services.purchase_value_correction_moving_average_peer_source_loader import (
    PurchaseValueCorrectionMovingAveragePeerSource,
    load_purchase_value_correction_moving_average_peer_source,
)
from app.services.purchase_value_correction_moving_average_target_reconciliation_service import (
    reconcile_purchase_value_correction_moving_average_targets,
)
from app.services.purchase_value_correction_moving_average_replay_persistence_service import (
    PurchaseValueCorrectionMovingAverageReplayChronologyError,
    PurchaseValueCorrectionMovingAverageReplayDataIntegrityError,
    PurchaseValueCorrectionMovingAverageReplayTarget,
    _active_originals,
    _event_key,
    _load_history_for_update,
    _same_state_except_date,
    _target_key,
    _validate_history,
    reconcile_purchase_value_correction_moving_average_replay_source,
)


@dataclass(
    frozen=True,
    slots=True,
)
class PurchaseValueCorrectionMovingAveragePeerReconciliationResult:
    anchor_allocation_event_id: int
    peer_source: PurchaseValueCorrectionMovingAveragePeerSource
    peer_replay_result: (
        PurchaseValueCorrectionMovingAveragePeerReplayResult
    )
    created_events: tuple[
        PurchaseValueCorrectionMovingAverageReplayEvent,
        ...,
    ]


def _issued_primary_date(
    *,
    source: PurchaseValueCorrectionMovingAveragePeerSource,
    impact: MovingAveragePeerAttributedImpact,
) -> date:
    movement_id = (
        impact.source_moving_average_movement_id
    )

    matches = tuple(
        movement
        for movement in source.base_source.later_movements
        if movement.movement_id == movement_id
    )

    if len(matches) != 1:
        raise (
            PurchaseValueCorrectionMovingAverageReplayDataIntegrityError(
                "Peer issued impact does not resolve to exactly "
                "one MA ISSUE movement"
            )
        )

    return max(
        impact.recognition_date,
        matches[0].movement_date,
    )


def _target_from_peer_impact(
    *,
    source: PurchaseValueCorrectionMovingAveragePeerSource,
    impact: MovingAveragePeerAttributedImpact,
) -> PurchaseValueCorrectionMovingAverageReplayTarget:
    if impact.effect_kind == "issued":
        recognition_date = _issued_primary_date(
            source=source,
            impact=impact,
        )

    elif impact.effect_kind == "on_hand":
        recognition_date = impact.recognition_date

    else:
        raise (
            PurchaseValueCorrectionMovingAverageReplayDataIntegrityError(
                "Unsupported peer MA replay effect kind"
            )
        )

    return PurchaseValueCorrectionMovingAverageReplayTarget(
        purchase_value_correction_allocation_event_id=(
            impact.allocation_event_id
        ),
        product_id=source.product_id,
        warehouse_id=source.warehouse_id,
        effect_kind=impact.effect_kind,
        source_moving_average_movement_id=(
            impact.source_moving_average_movement_id
        ),
        source_inventory_cost_entry_id=(
            impact.source_inventory_cost_entry_id
        ),
        recognition_date=recognition_date,
        quantity=impact.quantity,
        original_valuation_amount=(
            impact.original_valuation_amount
        ),
        corrected_valuation_amount=(
            impact.corrected_valuation_amount
        ),
        currency_code=(
            source.base_source.currency_code
        ),
    )


def _target_from_composed_peer_impact(
    *,
    source: PurchaseValueCorrectionMovingAveragePeerSource,
    allocation_event_id: int,
    impact: PurchaseValueCorrectionMovingAverageComposedImpact,
) -> PurchaseValueCorrectionMovingAverageReplayTarget:
    """
    Map one final routed marginal peer impact into existing immutable
    replay persistence.

    The allocation remains owned by the peer, while warehouse,
    recognition date and downstream ISSUE provenance come from the
    composed cross-warehouse result.
    """
    if impact.company_id != source.company_id:
        raise (
            PurchaseValueCorrectionMovingAverageReplayDataIntegrityError(
                "Composed peer MA impact company does not match source"
            )
        )

    if impact.product_id != source.product_id:
        raise (
            PurchaseValueCorrectionMovingAverageReplayDataIntegrityError(
                "Composed peer MA impact product does not match source"
            )
        )

    return PurchaseValueCorrectionMovingAverageReplayTarget(
        purchase_value_correction_allocation_event_id=(
            allocation_event_id
        ),
        product_id=impact.product_id,
        warehouse_id=impact.warehouse_id,
        effect_kind=impact.effect_kind,
        source_moving_average_movement_id=(
            impact.source_moving_average_movement_id
        ),
        source_inventory_cost_entry_id=(
            impact.source_inventory_cost_entry_id
        ),
        recognition_date=impact.recognition_date,
        quantity=impact.quantity,
        original_valuation_amount=(
            impact.original_valuation_amount
        ),
        corrected_valuation_amount=(
            impact.corrected_valuation_amount
        ),
        currency_code=(
            source.base_source.currency_code
        ),
    )


def _removal_target(
    event: PurchaseValueCorrectionMovingAverageReplayEvent,
) -> PurchaseValueCorrectionMovingAverageReplayTarget:
    amount = event.corrected_valuation_amount

    return PurchaseValueCorrectionMovingAverageReplayTarget(
        purchase_value_correction_allocation_event_id=(
            event
            .purchase_value_correction_allocation_event_id
        ),
        product_id=event.product_id,
        warehouse_id=event.warehouse_id,
        effect_kind=event.effect_kind,
        source_moving_average_movement_id=(
            event.source_moving_average_movement_id
        ),
        source_inventory_cost_entry_id=(
            event.source_inventory_cost_entry_id
        ),
        recognition_date=event.recognition_date,
        quantity=event.quantity,
        original_valuation_amount=amount,
        corrected_valuation_amount=amount,
        currency_code=event.currency_code,
    )


async def _reconcile_one_peer(
    db: AsyncSession,
    *,
    company_id: int,
    allocation_event_id: int,
    desired_targets: tuple[
        PurchaseValueCorrectionMovingAverageReplayTarget,
        ...,
    ],
    adjustment_date: date,
    created_by: int,
) -> tuple[
    PurchaseValueCorrectionMovingAverageReplayEvent,
    ...,
]:
    return await reconcile_purchase_value_correction_moving_average_targets(
        db,
        company_id=company_id,
        allocation_event_id=allocation_event_id,
        desired_targets=desired_targets,
        adjustment_date=adjustment_date,
        created_by=created_by,
    )


async def reconcile_purchase_value_correction_moving_average_peers(
    db: AsyncSession,
    *,
    company_id: int,
    anchor_allocation_event_id: int,
    adjustment_date: date,
    created_by: int,
) -> PurchaseValueCorrectionMovingAveragePeerReconciliationResult:
    """
    Peer-aware immutable reconciliation for every active PVCA
    affecting one physical MA receipt.

    Pure peer replay calculates cumulative chronology and marginal
    per-PVCA attribution.

    Each allocation owns only its marginal impacts.

    Existing independent history is corrected forward-only through:
        original -> reversal -> replacement

    Caller owns COMMIT / ROLLBACK.
    """

    if not isinstance(adjustment_date, date):
        raise (
            PurchaseValueCorrectionMovingAverageReplayChronologyError(
                "adjustment_date must be a date"
            )
        )

    source = (
        await load_purchase_value_correction_moving_average_peer_source(
            db,
            company_id=company_id,
            allocation_event_id=(
                anchor_allocation_event_id
            ),
        )
    )

    base = source.base_source

    peer_replay = (
        calculate_purchase_value_correction_moving_average_peer_replay(
            opening_quantity=base.opening_quantity,
            opening_inventory_value=(
                base.opening_inventory_value
            ),
            receipt_quantity=base.receipt_quantity,
            original_receipt_value=(
                base.original_receipt_value
            ),
            historical_receipt_balance_quantity_after=(
                base
                .historical_receipt_balance_quantity_after
            ),
            historical_receipt_balance_value_after=(
                base
                .historical_receipt_balance_value_after
            ),
            historical_receipt_average_unit_cost_after=(
                base
                .historical_receipt_average_unit_cost_after
            ),
            later_movements=base.later_movements,
            peers=source.peers,
        )
    )

    steps_by_allocation = {}

    for step in peer_replay.steps:
        allocation_id = (
            step.peer.allocation_event_id
        )

        if allocation_id in steps_by_allocation:
            raise (
                PurchaseValueCorrectionMovingAverageReplayDataIntegrityError(
                    "Peer replay returned duplicate allocation step"
                )
            )

        steps_by_allocation[
            allocation_id
        ] = step

    created = []

    for peer in peer_replay.ordered_peers:
        step = steps_by_allocation.get(
            peer.allocation_event_id
        )

        if step is None:
            raise (
                PurchaseValueCorrectionMovingAverageReplayDataIntegrityError(
                    "Peer replay step is missing"
                )
            )

        marginal_replay_result = (
            PurchaseValueCorrectionMovingAverageReplayResult(
                receipt_value_delta=(
                    step.attributed_delta_total
                ),
                impacts=tuple(
                    step.attributed_impacts
                ),
                replay_states=(),
                final_quantity=(
                    step.replay_result.final_quantity
                ),
                original_final_value=(
                    step.replay_result.original_final_value
                ),
                corrected_final_value=(
                    step.replay_result.corrected_final_value
                ),
            )
        )

        peer_base_source = replace(
            base,
            recognition_date=(
                peer.recognition_date
            ),
        )

        composition = (
            await compose_purchase_value_correction_moving_average_transfer_replay(
                db,
                source=peer_base_source,
                source_replay_result=(
                    marginal_replay_result
                ),
            )
        )

        targets = tuple(
            _target_from_composed_peer_impact(
                source=source,
                allocation_event_id=(
                    peer.allocation_event_id
                ),
                impact=impact,
            )
            for impact in composition.impacts
        )

        rows = await _reconcile_one_peer(
            db,
            company_id=company_id,
            allocation_event_id=(
                peer.allocation_event_id
            ),
            desired_targets=targets,
            adjustment_date=adjustment_date,
            created_by=created_by,
        )

        created.extend(rows)

    return (
        PurchaseValueCorrectionMovingAveragePeerReconciliationResult(
            anchor_allocation_event_id=(
                anchor_allocation_event_id
            ),
            peer_source=source,
            peer_replay_result=peer_replay,
            created_events=tuple(created),
        )
    )
