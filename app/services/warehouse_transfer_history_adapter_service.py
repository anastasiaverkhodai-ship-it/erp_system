from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Iterable

from app.models.warehouse_transfer_event import (
    WarehouseTransferEvent,
)
from app.models.warehouse_transfer_line import (
    WarehouseTransferLine,
)
from app.services.warehouse_transfer_history_service import (
    WarehouseTransferHistoryError,
    WarehouseTransferHistoryItem,
    WarehouseTransferLineTarget,
    normalize_history_key,
    normalize_transfer_target,
)


class WarehouseTransferHistoryAdapterError(ValueError):
    pass


def warehouse_transfer_rows_to_history(
    *,
    events: Iterable[WarehouseTransferEvent],
    lines: Iterable[WarehouseTransferLine],
    history_key: str,
) -> tuple[WarehouseTransferHistoryItem, ...]:
    """
    Convert persisted immutable ORM rows into pure planner history.

    Only rows from one correction chain are accepted.

    Original/replacement events require physical paired transfer lines.
    Reversal metadata rows have target=None because they are not a new
    business target.
    """

    history_key = normalize_history_key(
        history_key
    )

    event_rows = tuple(events)
    line_rows = tuple(lines)

    event_by_id: dict[
        int,
        WarehouseTransferEvent,
    ] = {}

    for event in event_rows:
        if event.history_key != history_key:
            raise WarehouseTransferHistoryAdapterError(
                "event belongs to a different history_key"
            )

        if event.id is None:
            raise WarehouseTransferHistoryAdapterError(
                "persisted event id is required"
            )

        if event.id in event_by_id:
            raise WarehouseTransferHistoryAdapterError(
                f"duplicate event id: {event.id}"
            )

        event_by_id[event.id] = event

    lines_by_event: dict[
        int,
        list[WarehouseTransferLine],
    ] = defaultdict(list)

    for line in line_rows:
        if line.transfer_event_id not in event_by_id:
            raise WarehouseTransferHistoryAdapterError(
                "orphan transfer line references event outside "
                "the locked history chain"
            )

        lines_by_event[
            line.transfer_event_id
        ].append(line)

    result: list[
        WarehouseTransferHistoryItem
    ] = []

    for event in sorted(
        event_rows,
        key=lambda row: row.id,
    ):
        if event.reversal_of_id is not None:
            result.append(
                WarehouseTransferHistoryItem(
                    id=event.id,
                    history_key=history_key,
                    target=None,
                    reversal_of_id=event.reversal_of_id,
                )
            )
            continue

        event_lines = lines_by_event.get(
            event.id,
            [],
        )

        if not event_lines:
            raise WarehouseTransferHistoryAdapterError(
                "original transfer event requires at least "
                "one paired physical line"
            )

        seen_products: set[int] = set()
        pure_lines: list[
            WarehouseTransferLineTarget
        ] = []

        for line in event_lines:
            if line.company_id != event.company_id:
                raise WarehouseTransferHistoryAdapterError(
                    "transfer line company does not match event"
                )

            if (
                line.source_warehouse_id
                != event.source_warehouse_id
            ):
                raise WarehouseTransferHistoryAdapterError(
                    "transfer line source warehouse does not "
                    "match event"
                )

            if (
                line.destination_warehouse_id
                != event.destination_warehouse_id
            ):
                raise WarehouseTransferHistoryAdapterError(
                    "transfer line destination warehouse does "
                    "not match event"
                )

            if line.product_id in seen_products:
                raise WarehouseTransferHistoryAdapterError(
                    "duplicate product inside one transfer event"
                )

            seen_products.add(
                line.product_id
            )

            quantity = Decimal(
                line.quantity
            )

            if (
                not quantity.is_finite()
                or quantity <= 0
            ):
                raise WarehouseTransferHistoryAdapterError(
                    "persisted transfer line quantity must be "
                    "positive"
                )

            pure_lines.append(
                WarehouseTransferLineTarget(
                    product_id=line.product_id,
                    quantity=quantity,
                )
            )

        try:
            target = normalize_transfer_target(
                company_id=event.company_id,
                source_warehouse_id=(
                    event.source_warehouse_id
                ),
                destination_warehouse_id=(
                    event.destination_warehouse_id
                ),
                transfer_date=event.transfer_date,
                lines=pure_lines,
            )
        except WarehouseTransferHistoryError as exc:
            raise WarehouseTransferHistoryAdapterError(
                str(exc)
            ) from exc

        result.append(
            WarehouseTransferHistoryItem(
                id=event.id,
                history_key=history_key,
                target=target,
                reversal_of_id=None,
            )
        )

    return tuple(result)
