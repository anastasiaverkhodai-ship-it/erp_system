from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import DocumentType
from app.models.inventory_count_variance_event import (
    InventoryCountVarianceDirection,
    InventoryCountVarianceEvent,
)
from app.services.inventory_count_history_service import (
    InventoryCountHistoryAction,
    InventoryCountHistoryPlan,
    InventoryCountTarget,
)
from app.services.inventory_count_persistence_service import (
    append_inventory_count_event,
)
from app.services.inventory_count_physical_factory import (
    InventoryCountPhysicalDirection,
    InventoryCountPhysicalFactory,
)
from app.services.inventory_count_reconciliation_service import (
    prepare_inventory_count_reconciliation,
)


class InventoryCountExecutionError(ValueError):
    pass


async def _append_variance_event(
    db: AsyncSession,
    *,
    count_event_id: int,
    target: InventoryCountTarget,
    physical,
) -> InventoryCountVarianceEvent:
    variance = target.variance_quantity

    if variance == 0:
        raise InventoryCountExecutionError(
            "zero variance must not create variance event"
        )

    expected_direction = (
        InventoryCountPhysicalDirection.INCREASE
        if variance > 0
        else InventoryCountPhysicalDirection.DECREASE
    )

    expected_quantity = abs(
        Decimal(variance)
    )

    if physical.direction != expected_direction:
        raise InventoryCountExecutionError(
            "physical adjustment direction does not match variance"
        )

    if Decimal(physical.quantity) != expected_quantity:
        raise InventoryCountExecutionError(
            "physical adjustment quantity does not match variance"
        )

    if physical.product_id != target.product_id:
        raise InventoryCountExecutionError(
            "physical adjustment product does not match target"
        )

    if physical.warehouse_id != target.warehouse_id:
        raise InventoryCountExecutionError(
            "physical adjustment warehouse does not match target"
        )

    direction = (
        InventoryCountVarianceDirection.INCREASE
        if variance > 0
        else InventoryCountVarianceDirection.DECREASE
    )

    row = InventoryCountVarianceEvent(
        company_id=target.company_id,
        inventory_count_event_id=count_event_id,
        document_id=physical.document_id,
        document_line_id=physical.document_line_id,
        document_type=DocumentType.ADJUSTMENT,
        product_id=target.product_id,
        warehouse_id=target.warehouse_id,
        adjustment_date=target.count_date,
        direction=direction,
        quantity=expected_quantity,
    )

    db.add(row)
    await db.flush()

    return row


async def execute_inventory_count_reconciliation(
    db: AsyncSession,
    *,
    factory: InventoryCountPhysicalFactory,
    company_id: int,
    history_key: str,
    target: InventoryCountTarget | None,
    adjustment_date: date | None,
    created_by: int,
) -> InventoryCountHistoryPlan:
    """
    Execute immutable count reconciliation.

    Count snapshot itself is always distinct from its variance posting.

    Transaction boundary belongs to caller.
    """

    plan = await prepare_inventory_count_reconciliation(
        db,
        company_id=company_id,
        history_key=history_key,
        target=target,
        adjustment_date=adjustment_date,
    )

    if plan.action == InventoryCountHistoryAction.NOOP:
        return plan

    original_event = plan.active_original

    if plan.action in {
        InventoryCountHistoryAction.REVERSE,
        InventoryCountHistoryAction.REVERSE_AND_REPLACE,
    }:
        if original_event is None:
            raise InventoryCountExecutionError(
                "reversal requires active original"
            )

        if plan.reversal_date is None:
            raise InventoryCountExecutionError(
                "reversal_date is required"
            )

        await factory.reverse_variance_adjustment(
            db,
            original_event=original_event,
            reversal_date=plan.reversal_date,
            created_by=created_by,
        )

        await append_inventory_count_event(
            db,
            company_id=original_event.company_id,
            history_key=plan.history_key,
            product_id=original_event.product_id,
            warehouse_id=original_event.warehouse_id,
            count_date=plan.reversal_date,
            expected_quantity=(
                original_event.expected_quantity
            ),
            counted_quantity=(
                original_event.counted_quantity
            ),
            created_by=created_by,
            reversal_of_id=original_event.id,
        )

    if plan.action in {
        InventoryCountHistoryAction.CREATE,
        InventoryCountHistoryAction.REVERSE_AND_REPLACE,
    }:
        if plan.target is None:
            raise InventoryCountExecutionError(
                "create/replacement requires target"
            )

        count_event = await append_inventory_count_event(
            db,
            company_id=plan.target.company_id,
            history_key=plan.history_key,
            product_id=plan.target.product_id,
            warehouse_id=plan.target.warehouse_id,
            count_date=plan.target.count_date,
            expected_quantity=(
                plan.target.expected_quantity
            ),
            counted_quantity=(
                plan.target.counted_quantity
            ),
            created_by=created_by,
        )

        physical = await factory.create_variance_adjustment(
            db,
            target=plan.target,
            created_by=created_by,
        )

        variance = plan.target.variance_quantity

        if variance == 0:
            if physical is not None:
                raise InventoryCountExecutionError(
                    "zero variance must not create physical adjustment"
                )

            return plan

        if physical is None:
            raise InventoryCountExecutionError(
                "non-zero variance requires physical adjustment"
            )

        await _append_variance_event(
            db,
            count_event_id=count_event.id,
            target=plan.target,
            physical=physical,
        )

    return plan
