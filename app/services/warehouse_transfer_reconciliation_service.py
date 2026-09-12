from __future__ import annotations

from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.warehouse_transfer_history_adapter_service import (
    warehouse_transfer_rows_to_history,
)
from app.services.warehouse_transfer_history_service import (
    WarehouseTransferHistoryPlan,
    WarehouseTransferTarget,
    normalize_history_key,
    plan_warehouse_transfer_history,
)
from app.services.warehouse_transfer_persistence_service import (
    load_warehouse_transfer_lines,
    lock_warehouse_transfer_history,
)


async def prepare_warehouse_transfer_reconciliation(
    db: AsyncSession,
    *,
    company_id: int,
    history_key: str,
    target: WarehouseTransferTarget | None,
    adjustment_date: date | None = None,
) -> WarehouseTransferHistoryPlan:
    """
    Lock one correction chain, reconstruct immutable history, and
    return the pure reconciliation decision.

    This function performs no posting and no persistence writes.
    """

    history_key = normalize_history_key(
        history_key
    )

    events = await lock_warehouse_transfer_history(
        db,
        company_id=company_id,
        history_key=history_key,
    )

    event_ids = tuple(
        event.id
        for event in events
    )

    lines = await load_warehouse_transfer_lines(
        db,
        transfer_event_ids=event_ids,
    )

    history = warehouse_transfer_rows_to_history(
        events=events,
        lines=lines,
        history_key=history_key,
    )

    return plan_warehouse_transfer_history(
        history=history,
        history_key=history_key,
        target=target,
        adjustment_date=adjustment_date,
    )
