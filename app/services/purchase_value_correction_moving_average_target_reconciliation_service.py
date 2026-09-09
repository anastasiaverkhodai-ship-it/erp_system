from dataclasses import replace
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.purchase_value_correction_moving_average_replay_event import (
    PurchaseValueCorrectionMovingAverageReplayEvent,
)
from app.services.purchase_value_correction_moving_average_replay_persistence_service import (
    PurchaseValueCorrectionMovingAverageReplayChronologyError,
    PurchaseValueCorrectionMovingAverageReplayDataIntegrityError,
    PurchaseValueCorrectionMovingAverageReplayTarget,
    _active_originals,
    _event_key,
    _load_history_for_update,
    _target_key,
    _validate_history,
    reconcile_purchase_value_correction_moving_average_replay_source,
)
from app.services.purchase_value_correction_moving_average_replay_reconciliation_service import (
    _removal_target,
    _same_state_except_date,
)


async def reconcile_purchase_value_correction_moving_average_targets(
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
    """
    Generic immutable reconciliation of one PVC MA allocation
    against a complete desired destination state.

    Semantics:
        removed -> reversal on adjustment_date
        changed -> reversal + replacement on adjustment_date
        unchanged -> no churn, persisted date preserved
        new destination after prior history -> adjustment_date

    Caller owns COMMIT / ROLLBACK.
    """

    if not isinstance(adjustment_date, date):
        raise PurchaseValueCorrectionMovingAverageReplayChronologyError(
            "adjustment_date must be a date"
        )

    history = await _load_history_for_update(
        db,
        company_id=company_id,
        allocation_event_id=allocation_event_id,
    )

    _validate_history(history)

    active = _active_originals(history)

    active_by_key = {}

    for event in active:
        key = _event_key(event)

        if key in active_by_key:
            raise PurchaseValueCorrectionMovingAverageReplayDataIntegrityError(
                "Multiple active MA replay events exist "
                "for one destination"
            )

        active_by_key[key] = event

    desired_by_key = {}

    for target in desired_targets:
        if (
            target.purchase_value_correction_allocation_event_id
            != allocation_event_id
        ):
            raise PurchaseValueCorrectionMovingAverageReplayDataIntegrityError(
                "Desired MA replay target belongs to "
                "another allocation"
            )

        key = _target_key(target)

        if key in desired_by_key:
            raise PurchaseValueCorrectionMovingAverageReplayDataIntegrityError(
                "Duplicate desired MA replay destination"
            )

        desired_by_key[key] = target

    created = []

    removed = tuple(
        event
        for key, event in active_by_key.items()
        if key not in desired_by_key
    )

    for current in sorted(
        removed,
        key=lambda event: (
            event.effect_kind,
            event.source_moving_average_movement_id or 0,
            event.source_inventory_cost_entry_id or 0,
        ),
    ):
        if adjustment_date < current.recognition_date:
            raise PurchaseValueCorrectionMovingAverageReplayChronologyError(
                "adjustment_date predates removed MA replay impact"
            )

        rows = (
            await reconcile_purchase_value_correction_moving_average_replay_source(
                db,
                company_id=company_id,
                target=_removal_target(current),
                created_by=created_by,
                reversal_date=adjustment_date,
            )
        )

        created.extend(rows)

    has_history = bool(history)

    for primary in sorted(
        desired_targets,
        key=lambda target: (
            target.effect_kind,
            target.source_moving_average_movement_id or 0,
            target.source_inventory_cost_entry_id or 0,
        ),
    ):
        key = _target_key(primary)

        current = active_by_key.get(key)

        target = primary
        reversal_date = None

        if current is not None:
            if _same_state_except_date(
                event=current,
                target=target,
            ):
                target = replace(
                    target,
                    recognition_date=current.recognition_date,
                )

            else:
                if (
                    adjustment_date < current.recognition_date
                    or adjustment_date < target.recognition_date
                ):
                    raise PurchaseValueCorrectionMovingAverageReplayChronologyError(
                        "adjustment_date predates changed "
                        "MA replay impact"
                    )

                reversal_date = adjustment_date

        elif has_history:
            if adjustment_date < target.recognition_date:
                raise PurchaseValueCorrectionMovingAverageReplayChronologyError(
                    "new MA replay destination after history "
                    "cannot be backdated"
                )

            target = replace(
                target,
                recognition_date=adjustment_date,
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

        created.extend(rows)

    return tuple(created)
