from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Iterable
from uuid import UUID


class WarehouseTransferHistoryError(ValueError):
    pass


class WarehouseTransferHistoryAction(str, Enum):
    CREATE = "create"
    NOOP = "noop"
    REVERSE = "reverse"
    REVERSE_AND_REPLACE = "reverse_and_replace"


@dataclass(frozen=True)
class WarehouseTransferLineTarget:
    product_id: int
    quantity: Decimal


@dataclass(frozen=True)
class WarehouseTransferTarget:
    company_id: int
    source_warehouse_id: int
    destination_warehouse_id: int
    transfer_date: date
    lines: tuple[WarehouseTransferLineTarget, ...]


@dataclass(frozen=True)
class WarehouseTransferHistoryItem:
    id: int
    history_key: str
    target: WarehouseTransferTarget | None
    reversal_of_id: int | None


@dataclass(frozen=True)
class WarehouseTransferHistoryPlan:
    history_key: str
    action: WarehouseTransferHistoryAction
    active_original: WarehouseTransferHistoryItem | None
    target: WarehouseTransferTarget | None
    reversal_date: date | None


def normalize_history_key(value: str) -> str:
    if not isinstance(value, str):
        raise WarehouseTransferHistoryError(
            "history_key must be a string UUID"
        )

    try:
        parsed = UUID(value)
    except (ValueError, AttributeError) as exc:
        raise WarehouseTransferHistoryError(
            "history_key must be a valid UUID"
        ) from exc

    canonical = str(parsed)

    if value != canonical:
        raise WarehouseTransferHistoryError(
            "history_key must use canonical UUID format"
        )

    return canonical


def _positive_id(value: int, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise WarehouseTransferHistoryError(
            f"{field} must be an integer"
        )

    if value <= 0:
        raise WarehouseTransferHistoryError(
            f"{field} must be greater than zero"
        )

    return value


def _positive_quantity(
    value: Decimal,
    *,
    field: str,
) -> Decimal:
    try:
        result = Decimal(value)
    except Exception as exc:
        raise WarehouseTransferHistoryError(
            f"{field} must be a valid decimal"
        ) from exc

    if not result.is_finite() or result <= 0:
        raise WarehouseTransferHistoryError(
            f"{field} must be greater than zero"
        )

    return result


def normalize_transfer_target(
    *,
    company_id: int,
    source_warehouse_id: int,
    destination_warehouse_id: int,
    transfer_date: date,
    lines: Iterable[WarehouseTransferLineTarget],
) -> WarehouseTransferTarget:
    company_id = _positive_id(
        company_id,
        field="company_id",
    )
    source_warehouse_id = _positive_id(
        source_warehouse_id,
        field="source_warehouse_id",
    )
    destination_warehouse_id = _positive_id(
        destination_warehouse_id,
        field="destination_warehouse_id",
    )

    if source_warehouse_id == destination_warehouse_id:
        raise WarehouseTransferHistoryError(
            "source and destination warehouses must differ"
        )

    if not isinstance(transfer_date, date):
        raise WarehouseTransferHistoryError(
            "transfer_date must be a date"
        )

    normalized_lines = []
    seen_products = set()

    for line in lines:
        product_id = _positive_id(
            line.product_id,
            field="product_id",
        )

        if product_id in seen_products:
            raise WarehouseTransferHistoryError(
                f"duplicate product_id in transfer target: "
                f"{product_id}"
            )

        seen_products.add(product_id)

        normalized_lines.append(
            WarehouseTransferLineTarget(
                product_id=product_id,
                quantity=_positive_quantity(
                    line.quantity,
                    field="quantity",
                ),
            )
        )

    if not normalized_lines:
        raise WarehouseTransferHistoryError(
            "transfer target must contain at least one line"
        )

    normalized_lines.sort(
        key=lambda item: item.product_id
    )

    return WarehouseTransferTarget(
        company_id=company_id,
        source_warehouse_id=source_warehouse_id,
        destination_warehouse_id=destination_warehouse_id,
        transfer_date=transfer_date,
        lines=tuple(normalized_lines),
    )


def resolve_active_transfer_original(
    history: Iterable[WarehouseTransferHistoryItem],
    *,
    history_key: str,
) -> WarehouseTransferHistoryItem | None:
    history_key = normalize_history_key(history_key)

    rows = tuple(
        row
        for row in history
        if normalize_history_key(row.history_key)
        == history_key
    )

    originals: dict[int, WarehouseTransferHistoryItem] = {}
    reversed_ids: set[int] = set()

    for row in rows:
        row_id = _positive_id(
            row.id,
            field="history id",
        )

        if row.reversal_of_id is None:
            if row.target is None:
                raise WarehouseTransferHistoryError(
                    "original history row requires a target"
                )

            if row_id in originals:
                raise WarehouseTransferHistoryError(
                    f"duplicate history id: {row_id}"
                )

            originals[row_id] = row
            continue

        reversal_of_id = _positive_id(
            row.reversal_of_id,
            field="reversal_of_id",
        )

        if reversal_of_id == row_id:
            raise WarehouseTransferHistoryError(
                "history row cannot reverse itself"
            )

        if reversal_of_id in reversed_ids:
            raise WarehouseTransferHistoryError(
                f"original {reversal_of_id} is reversed "
                f"more than once"
            )

        reversed_ids.add(reversal_of_id)

    unknown = reversed_ids - set(originals)

    if unknown:
        raise WarehouseTransferHistoryError(
            "reversal references unknown original ids "
            f"inside history chain: {sorted(unknown)}"
        )

    active = [
        row
        for row_id, row in originals.items()
        if row_id not in reversed_ids
    ]

    if len(active) > 1:
        raise WarehouseTransferHistoryError(
            "multiple ACTIVE warehouse transfer originals "
            "inside one history chain"
        )

    if not active:
        return None

    return active[0]


def plan_warehouse_transfer_history(
    *,
    history: Iterable[WarehouseTransferHistoryItem],
    history_key: str,
    target: WarehouseTransferTarget | None,
    adjustment_date: date | None = None,
) -> WarehouseTransferHistoryPlan:
    history_key = normalize_history_key(history_key)

    active = resolve_active_transfer_original(
        history,
        history_key=history_key,
    )

    if active is None:
        if target is None:
            return WarehouseTransferHistoryPlan(
                history_key=history_key,
                action=WarehouseTransferHistoryAction.NOOP,
                active_original=None,
                target=None,
                reversal_date=None,
            )

        return WarehouseTransferHistoryPlan(
            history_key=history_key,
            action=WarehouseTransferHistoryAction.CREATE,
            active_original=None,
            target=target,
            reversal_date=None,
        )

    if target == active.target:
        return WarehouseTransferHistoryPlan(
            history_key=history_key,
            action=WarehouseTransferHistoryAction.NOOP,
            active_original=active,
            target=target,
            reversal_date=None,
        )

    if adjustment_date is None:
        raise WarehouseTransferHistoryError(
            "adjustment_date is required when an ACTIVE "
            "transfer must be reversed"
        )

    if adjustment_date < active.target.transfer_date:
        raise WarehouseTransferHistoryError(
            "adjustment_date cannot predate active transfer"
        )

    if target is None:
        return WarehouseTransferHistoryPlan(
            history_key=history_key,
            action=WarehouseTransferHistoryAction.REVERSE,
            active_original=active,
            target=None,
            reversal_date=adjustment_date,
        )

    if target.transfer_date < active.target.transfer_date:
        raise WarehouseTransferHistoryError(
            "replacement transfer cannot move backward in time"
        )

    return WarehouseTransferHistoryPlan(
        history_key=history_key,
        action=(
            WarehouseTransferHistoryAction
            .REVERSE_AND_REPLACE
        ),
        active_original=active,
        target=target,
        reversal_date=adjustment_date,
    )
