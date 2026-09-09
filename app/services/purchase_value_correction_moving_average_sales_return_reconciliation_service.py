from dataclasses import dataclass
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.purchase_value_correction_moving_average_replay_event import (
    PurchaseValueCorrectionMovingAverageReplayEvent,
)
from app.services.purchase_value_correction_moving_average_replay_persistence_service import (
    PurchaseValueCorrectionMovingAverageReplayTarget,
)
from app.services.purchase_value_correction_moving_average_sales_return_baseline_service import (
    load_purchase_value_correction_moving_average_sales_return_baseline,
)
from app.services.purchase_value_correction_moving_average_sales_return_source_loader import (
    load_purchase_value_correction_moving_average_sales_return_source,
)
from app.services.purchase_value_correction_moving_average_sales_return_target_service import (
    build_purchase_value_correction_moving_average_sales_return_targets,
)
from app.services.purchase_value_correction_moving_average_target_reconciliation_service import (
    reconcile_purchase_value_correction_moving_average_targets,
)
from app.services.purchase_value_correction_moving_average_return_calculation_service import (
    MovingAverageReturnCandidate,
)


class PurchaseValueCorrectionMovingAverageSalesReturnReconciliationError(
    Exception
):
    pass


@dataclass(
    frozen=True,
    slots=True,
)
class PurchaseValueCorrectionMovingAverageSalesReturnReconciliationResult:
    cost_restoration_event_id: int
    inventory_cost_entry_id: int

    created_events: tuple[
        PurchaseValueCorrectionMovingAverageReplayEvent,
        ...,
    ]


async def reconcile_purchase_value_correction_moving_average_sales_return(
    db: AsyncSession,
    *,
    company_id: int,
    cost_restoration_event_id: int,
    adjustment_date: date,
    created_by: int,
) -> PurchaseValueCorrectionMovingAverageSalesReturnReconciliationResult:
    """
    Reclassify active PVC MA impact after Sales Return.

    fresh immutable peer replay baseline
        + aggregate ACTIVE Sales Return history for exact ICE
        -> desired issued residual + on_hand
        -> existing immutable PVC MA persistence.

    Caller owns COMMIT / ROLLBACK.
    """

    if company_id <= 0:
        raise ValueError(
            "company_id must be greater than zero"
        )

    if cost_restoration_event_id <= 0:
        raise ValueError(
            "cost_restoration_event_id must be greater than zero"
        )

    if created_by <= 0:
        raise ValueError(
            "created_by must be greater than zero"
        )

    if not isinstance(adjustment_date, date):
        raise ValueError(
            "adjustment_date must be a date"
        )

    source = (
        await load_purchase_value_correction_moving_average_sales_return_source(
            db,
            company_id=company_id,
            sales_return_cost_restoration_event_id=cost_restoration_event_id,
        )
    )

    baseline = (
        await load_purchase_value_correction_moving_average_sales_return_baseline(
            db,
            company_id=company_id,
            return_source=source,
        )
    )

    created = []

    for item in baseline.allocations:
        if item.issued is None:
            continue

        target_result = (
            build_purchase_value_correction_moving_average_sales_return_targets(
                baseline_issued=item.issued,
                baseline_on_hand=item.on_hand,
                active_return_candidates=(
                    tuple(
                MovingAverageReturnCandidate(
                    return_source_id=(
                        item.cost_restoration_event_id
                    ),
                    return_quantity=(
                        item.restored_quantity
                    ),
                )
                for item in source.active_return_candidates
            )
                ),
            )
        )

        desired = tuple(
            PurchaseValueCorrectionMovingAverageReplayTarget(
                purchase_value_correction_allocation_event_id=(
                    target.allocation_event_id
                ),
                product_id=source.product_id,
                warehouse_id=source.warehouse_id,
                effect_kind=target.effect_kind,
                source_moving_average_movement_id=(
                    target.source_moving_average_movement_id
                ),
                source_inventory_cost_entry_id=(
                    target.source_inventory_cost_entry_id
                ),
                recognition_date=adjustment_date,
                quantity=target.quantity,
                original_valuation_amount=(
                    target.original_valuation_amount
                ),
                corrected_valuation_amount=(
                    target.corrected_valuation_amount
                ),
                currency_code=target.currency_code,
            )
            for target in target_result.desired_targets
        )

        rows = (
            await reconcile_purchase_value_correction_moving_average_targets(
                db,
                company_id=company_id,
                allocation_event_id=item.allocation_event_id,
                desired_targets=desired,
                adjustment_date=adjustment_date,
                created_by=created_by,
            )
        )

        created.extend(rows)

    return (
        PurchaseValueCorrectionMovingAverageSalesReturnReconciliationResult(
            cost_restoration_event_id=cost_restoration_event_id,
            inventory_cost_entry_id=source.inventory_cost_entry_id,
            created_events=tuple(created),
        )
    )
