from __future__ import annotations

from typing import Iterable

from app.models.inventory_count_event import (
    InventoryCountEvent,
)
from app.services.inventory_count_history_service import (
    InventoryCountHistoryError,
    InventoryCountHistoryItem,
    normalize_history_key,
    normalize_inventory_count_target,
)


class InventoryCountHistoryAdapterError(ValueError):
    pass


def inventory_count_rows_to_history(
    *,
    events: Iterable[InventoryCountEvent],
    history_key: str,
) -> tuple[InventoryCountHistoryItem, ...]:
    """
    Convert persisted immutable count rows into pure planner history.

    Reversal rows carry target=None because they invalidate an earlier
    immutable observation rather than introducing a new target.
    """

    history_key = normalize_history_key(
        history_key
    )

    result: list[
        InventoryCountHistoryItem
    ] = []

    seen_ids: set[int] = set()

    for event in sorted(
        tuple(events),
        key=lambda row: row.id,
    ):
        if event.history_key != history_key:
            raise InventoryCountHistoryAdapterError(
                "event belongs to a different history_key"
            )

        if event.id is None:
            raise InventoryCountHistoryAdapterError(
                "persisted event id is required"
            )

        if event.id in seen_ids:
            raise InventoryCountHistoryAdapterError(
                f"duplicate event id: {event.id}"
            )

        seen_ids.add(
            event.id
        )

        if event.reversal_of_id is not None:
            result.append(
                InventoryCountHistoryItem(
                    id=event.id,
                    history_key=history_key,
                    target=None,
                    reversal_of_id=event.reversal_of_id,
                )
            )
            continue

        try:
            target = normalize_inventory_count_target(
                company_id=event.company_id,
                product_id=event.product_id,
                warehouse_id=event.warehouse_id,
                count_date=event.count_date,
                expected_quantity=event.expected_quantity,
                counted_quantity=event.counted_quantity,
            )
        except InventoryCountHistoryError as exc:
            raise InventoryCountHistoryAdapterError(
                str(exc)
            ) from exc

        result.append(
            InventoryCountHistoryItem(
                id=event.id,
                history_key=history_key,
                target=target,
                reversal_of_id=None,
            )
        )

    return tuple(result)
