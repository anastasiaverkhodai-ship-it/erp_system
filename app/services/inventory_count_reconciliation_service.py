from __future__ import annotations

from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.inventory_count_history_adapter_service import (
    inventory_count_rows_to_history,
)
from app.services.inventory_count_history_service import (
    InventoryCountHistoryPlan,
    InventoryCountTarget,
    normalize_history_key,
    plan_inventory_count_history,
)
from app.services.inventory_count_persistence_service import (
    lock_inventory_count_history,
)


async def prepare_inventory_count_reconciliation(
    db: AsyncSession,
    *,
    company_id: int,
    history_key: str,
    target: InventoryCountTarget | None,
    adjustment_date: date | None = None,
) -> InventoryCountHistoryPlan:
    """
    Lock one correction chain, reconstruct immutable history, and
    return the pure reconciliation decision.

    Count observation and physical variance posting remain separate.
    """

    history_key = normalize_history_key(
        history_key
    )

    events = await lock_inventory_count_history(
        db,
        company_id=company_id,
        history_key=history_key,
    )

    history = inventory_count_rows_to_history(
        events=events,
        history_key=history_key,
    )

    return plan_inventory_count_history(
        history=history,
        history_key=history_key,
        target=target,
        adjustment_date=adjustment_date,
    )
