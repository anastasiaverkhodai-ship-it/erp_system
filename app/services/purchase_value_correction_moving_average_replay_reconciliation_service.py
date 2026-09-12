from dataclasses import (
    dataclass,
    replace,
)
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.purchase_value_correction_moving_average_transfer_composition_service import (
    PurchaseValueCorrectionMovingAverageTransferCompositionResult,
    compose_purchase_value_correction_moving_average_transfer_replay,
)

from app.models.purchase_value_correction_moving_average_replay_event import (
    PurchaseValueCorrectionMovingAverageReplayEvent,
)
from app.services.purchase_value_correction_moving_average_replay_calculation_service import (
    MovingAverageReplayImpact,
    PurchaseValueCorrectionMovingAverageReplayResult,
    calculate_purchase_value_correction_moving_average_replay,
)
from app.services.purchase_value_correction_moving_average_replay_persistence_service import (
    PurchaseValueCorrectionMovingAverageReplayChronologyError,
    PurchaseValueCorrectionMovingAverageReplayDataIntegrityError,
    PurchaseValueCorrectionMovingAverageReplayTarget,
    _active_originals,
    _event_key,
    _same_state_except_date,
    _target_key,
    _validate_history,
    _load_history_for_update,
    reconcile_purchase_value_correction_moving_average_replay_source,
)
from app.services.purchase_value_correction_moving_average_replay_source_loader import (
    PurchaseValueCorrectionMovingAverageReplaySource,
    load_purchase_value_correction_moving_average_replay_source,
)


class PurchaseValueCorrectionMovingAverageReplayReconciliationError(
    Exception
):
    pass


@dataclass(
    frozen=True,
    slots=True,
)
class PurchaseValueCorrectionMovingAverageReplayReconciliationResult:
    allocation_event_id: int
    product_id: int
    warehouse_id: int

    replay_result: (
        PurchaseValueCorrectionMovingAverageReplayResult
    )

    desired_targets: tuple[
        PurchaseValueCorrectionMovingAverageReplayTarget,
        ...,
    ]

    reconciliation_targets: tuple[
        PurchaseValueCorrectionMovingAverageReplayTarget,
        ...,
    ]

    created_events: tuple[
        PurchaseValueCorrectionMovingAverageReplayEvent,
        ...,
    ]


def _impact_primary_date(
    *,
    source: PurchaseValueCorrectionMovingAverageReplaySource,
    impact: MovingAverageReplayImpact,
) -> date:
    if impact.effect_kind == "on_hand":
        return source.recognition_date

    if impact.effect_kind != "issued":
        raise PurchaseValueCorrectionMovingAverageReplayDataIntegrityError(
            "Unsupported MA replay effect kind"
        )

    movement_id = (
        impact.source_moving_average_movement_id
    )

    matches = tuple(
        movement
        for movement in source.later_movements
        if movement.movement_id == movement_id
    )

    if len(
        matches
    ) != 1:
        raise PurchaseValueCorrectionMovingAverageReplayDataIntegrityError(
            "Issued replay impact does not resolve to exactly one "
            "source MA movement"
        )

    return max(
        source.recognition_date,
        matches[0].movement_date,
    )


def _build_desired_targets(
    *,
    source: PurchaseValueCorrectionMovingAverageReplaySource,
    replay_result: PurchaseValueCorrectionMovingAverageReplayResult,
) -> tuple[
    PurchaseValueCorrectionMovingAverageReplayTarget,
    ...,
]:
    targets = []

    seen = set()

    for impact in replay_result.impacts:
        target = PurchaseValueCorrectionMovingAverageReplayTarget(
            purchase_value_correction_allocation_event_id=(
                source
                .purchase_value_correction_allocation_event_id
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
            recognition_date=_impact_primary_date(
                source=source,
                impact=impact,
            ),
            quantity=impact.quantity,
            original_valuation_amount=(
                impact.original_valuation_amount
            ),
            corrected_valuation_amount=(
                impact.corrected_valuation_amount
            ),
            currency_code=source.currency_code,
        )

        key = _target_key(
            target
        )

        if key in seen:
            raise PurchaseValueCorrectionMovingAverageReplayDataIntegrityError(
                "Pure MA replay produced duplicate destination key"
            )

        seen.add(
            key
        )

        targets.append(
            target
        )

    return tuple(
        sorted(
            targets,
            key=lambda target: (
                target.effect_kind,
                target.source_moving_average_movement_id
                or 0,
                target.source_inventory_cost_entry_id
                or 0,
            ),
        )
    )


def _build_composed_desired_targets(
    *,
    source: PurchaseValueCorrectionMovingAverageReplaySource,
    composition: PurchaseValueCorrectionMovingAverageTransferCompositionResult,
) -> tuple[
    PurchaseValueCorrectionMovingAverageReplayTarget,
    ...,
]:
    """
    Convert final cross-warehouse economic MA impacts into the
    existing immutable persistence target contract.

    Warehouse and recognition date come from the composed impact,
    not from the original receipt warehouse.
    """
    targets = []
    seen = set()

    for impact in composition.impacts:
        if impact.company_id != source.company_id:
            raise (
                PurchaseValueCorrectionMovingAverageReplayDataIntegrityError(
                    "Composed MA impact company does not match source"
                )
            )

        if impact.product_id != source.product_id:
            raise (
                PurchaseValueCorrectionMovingAverageReplayDataIntegrityError(
                    "Composed MA impact product does not match source"
                )
            )

        target = PurchaseValueCorrectionMovingAverageReplayTarget(
            purchase_value_correction_allocation_event_id=(
                source
                .purchase_value_correction_allocation_event_id
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
            currency_code=source.currency_code,
        )

        key = _target_key(
            target
        )

        if key in seen:
            raise (
                PurchaseValueCorrectionMovingAverageReplayDataIntegrityError(
                    "Composed MA replay produced duplicate "
                    "cross-warehouse destination key"
                )
            )

        seen.add(
            key
        )

        targets.append(
            target
        )

    return tuple(
        sorted(
            targets,
            key=lambda target: (
                target.warehouse_id,
                target.effect_kind,
                target.source_moving_average_movement_id
                or 0,
                target.source_inventory_cost_entry_id
                or 0,
            ),
        )
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


async def reconcile_purchase_value_correction_moving_average_replay(
    db: AsyncSession,
    *,
    company_id: int,
    allocation_event_id: int,
    adjustment_date: date,
    created_by: int,
) -> PurchaseValueCorrectionMovingAverageReplayReconciliationResult:
    """
    Reconcile current immutable PVC MA replay impacts.

    Primary chronology:
        new on_hand -> PVCA recognition date
        new issued -> max(PVCA recognition, ISSUE date)

    Forward-only topology/value correction:
        changed destination -> reversal + replacement on adjustment_date
        removed destination -> reversal on adjustment_date
        destination appearing after prior replay history ->
            recognition on adjustment_date

    Existing unchanged replacement keeps its persisted accounting date;
    no date churn.

    Caller owns COMMIT / ROLLBACK.
    """

    if not isinstance(
        adjustment_date,
        date,
    ):
        raise PurchaseValueCorrectionMovingAverageReplayChronologyError(
            "adjustment_date must be a date"
        )

    source = (
        await load_purchase_value_correction_moving_average_replay_source(
            db,
            company_id=company_id,
            allocation_event_id=allocation_event_id,
        )
    )

    replay_result = (
        calculate_purchase_value_correction_moving_average_replay(
            opening_quantity=source.opening_quantity,
            opening_inventory_value=(
                source.opening_inventory_value
            ),
            receipt_quantity=source.receipt_quantity,
            original_receipt_value=(
                source.original_receipt_value
            ),
            corrected_receipt_value=(
                source.corrected_receipt_value
            ),
            historical_receipt_balance_quantity_after=(
                source
                .historical_receipt_balance_quantity_after
            ),
            historical_receipt_balance_value_after=(
                source
                .historical_receipt_balance_value_after
            ),
            historical_receipt_average_unit_cost_after=(
                source
                .historical_receipt_average_unit_cost_after
            ),
            later_movements=source.later_movements,
        )
    )

    composition = (
        await compose_purchase_value_correction_moving_average_transfer_replay(
            db,
            source=source,
            source_replay_result=replay_result,
        )
    )

    desired = _build_composed_desired_targets(
        source=source,
        composition=composition,
    )

    history = await _load_history_for_update(
        db,
        company_id=company_id,
        allocation_event_id=allocation_event_id,
    )

    _validate_history(
        history
    )

    active = _active_originals(
        history
    )

    active_by_key = {}

    for event in active:
        key = _event_key(
            event
        )

        if key in active_by_key:
            raise PurchaseValueCorrectionMovingAverageReplayDataIntegrityError(
                "Multiple active MA replay events exist for one key"
            )

        active_by_key[
            key
        ] = event

    desired_by_key = {
        _target_key(
            target
        ): target
        for target in desired
    }

    created = []
    reconciliation_targets = []

    removed = tuple(
        event
        for key, event in active_by_key.items()
        if key not in desired_by_key
    )

    for current in sorted(
        removed,
        key=lambda event: (
            event.effect_kind,
            event.source_moving_average_movement_id
            or 0,
        ),
    ):
        if (
            adjustment_date
            < current.recognition_date
        ):
            raise PurchaseValueCorrectionMovingAverageReplayChronologyError(
                "adjustment_date cannot predate removed MA replay impact"
            )

        target = _removal_target(
            current
        )

        rows = (
            await reconcile_purchase_value_correction_moving_average_replay_source(
                db,
                company_id=company_id,
                target=target,
                created_by=created_by,
                reversal_date=adjustment_date,
            )
        )

        reconciliation_targets.append(
            target
        )
        created.extend(
            rows
        )

    has_any_history = bool(
        history
    )

    for primary_target in desired:
        key = _target_key(
            primary_target
        )

        current = active_by_key.get(
            key
        )

        target = primary_target
        reversal_date = None

        if current is not None:
            if _same_state_except_date(
                event=current,
                target=target,
            ):
                target = replace(
                    target,
                    recognition_date=(
                        current.recognition_date
                    ),
                )
            else:
                if (
                    adjustment_date
                    < current.recognition_date
                    or adjustment_date
                    < target.recognition_date
                ):
                    raise PurchaseValueCorrectionMovingAverageReplayChronologyError(
                        "adjustment_date cannot predate changed "
                        "MA replay impact"
                    )

                reversal_date = (
                    adjustment_date
                )

        elif has_any_history:
            if (
                adjustment_date
                < target.recognition_date
            ):
                raise PurchaseValueCorrectionMovingAverageReplayChronologyError(
                    "new destination after replay history "
                    "cannot be backdated"
                )

            target = replace(
                target,
                recognition_date=(
                    adjustment_date
                ),
            )

        rows = (
            await reconcile_purchase_value_correction_moving_average_replay_source(
                db,
                company_id=company_id,
                target=target,
                created_by=created_by,
                reversal_date=reversal_date,
            )
        )

        reconciliation_targets.append(
            target
        )
        created.extend(
            rows
        )

    return (
        PurchaseValueCorrectionMovingAverageReplayReconciliationResult(
            allocation_event_id=(
                allocation_event_id
            ),
            product_id=source.product_id,
            warehouse_id=source.warehouse_id,
            replay_result=replay_result,
            desired_targets=desired,
            reconciliation_targets=tuple(
                reconciliation_targets
            ),
            created_events=tuple(
                created
            ),
        )
    )
