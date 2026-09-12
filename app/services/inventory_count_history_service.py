from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Iterable
from uuid import UUID


class InventoryCountHistoryError(ValueError):
    pass


class InventoryCountHistoryAction(str, Enum):
    CREATE = "create"
    NOOP = "noop"
    REVERSE = "reverse"
    REVERSE_AND_REPLACE = "reverse_and_replace"


@dataclass(frozen=True)
class InventoryCountTarget:
    company_id: int
    product_id: int
    warehouse_id: int
    count_date: date
    expected_quantity: Decimal
    counted_quantity: Decimal

    @property
    def variance_quantity(self) -> Decimal:
        return (
            self.counted_quantity
            - self.expected_quantity
        )


@dataclass(frozen=True)
class InventoryCountHistoryItem:
    id: int
    history_key: str
    target: InventoryCountTarget | None
    reversal_of_id: int | None


@dataclass(frozen=True)
class InventoryCountHistoryPlan:
    history_key: str
    action: InventoryCountHistoryAction
    active_original: InventoryCountHistoryItem | None
    target: InventoryCountTarget | None
    reversal_date: date | None


def normalize_history_key(value: str) -> str:
    if not isinstance(value, str):
        raise InventoryCountHistoryError(
            "history_key must be a string UUID"
        )

    try:
        parsed = UUID(value)
    except (ValueError, AttributeError) as exc:
        raise InventoryCountHistoryError(
            "history_key must be a valid UUID"
        ) from exc

    canonical = str(parsed)

    if value != canonical:
        raise InventoryCountHistoryError(
            "history_key must use canonical UUID format"
        )

    return canonical


def _positive_id(value: int, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise InventoryCountHistoryError(
            f"{field} must be an integer"
        )

    if value <= 0:
        raise InventoryCountHistoryError(
            f"{field} must be greater than zero"
        )

    return value


def _nonnegative_quantity(
    value: Decimal,
    *,
    field: str,
) -> Decimal:
    try:
        result = Decimal(value)
    except Exception as exc:
        raise InventoryCountHistoryError(
            f"{field} must be a valid decimal"
        ) from exc

    if not result.is_finite() or result < 0:
        raise InventoryCountHistoryError(
            f"{field} cannot be negative"
        )

    return result


def normalize_inventory_count_target(
    *,
    company_id: int,
    product_id: int,
    warehouse_id: int,
    count_date: date,
    expected_quantity: Decimal,
    counted_quantity: Decimal,
) -> InventoryCountTarget:
    company_id = _positive_id(
        company_id,
        field="company_id",
    )
    product_id = _positive_id(
        product_id,
        field="product_id",
    )
    warehouse_id = _positive_id(
        warehouse_id,
        field="warehouse_id",
    )

    if not isinstance(count_date, date):
        raise InventoryCountHistoryError(
            "count_date must be a date"
        )

    expected_quantity = _nonnegative_quantity(
        expected_quantity,
        field="expected_quantity",
    )
    counted_quantity = _nonnegative_quantity(
        counted_quantity,
        field="counted_quantity",
    )

    return InventoryCountTarget(
        company_id=company_id,
        product_id=product_id,
        warehouse_id=warehouse_id,
        count_date=count_date,
        expected_quantity=expected_quantity,
        counted_quantity=counted_quantity,
    )


def resolve_active_inventory_count_original(
    history: Iterable[InventoryCountHistoryItem],
    *,
    history_key: str,
) -> InventoryCountHistoryItem | None:
    history_key = normalize_history_key(history_key)

    rows = tuple(
        row
        for row in history
        if normalize_history_key(row.history_key)
        == history_key
    )

    originals: dict[int, InventoryCountHistoryItem] = {}
    reversed_ids: set[int] = set()

    for row in rows:
        row_id = _positive_id(
            row.id,
            field="history id",
        )

        if row.reversal_of_id is None:
            if row.target is None:
                raise InventoryCountHistoryError(
                    "original history row requires a target"
                )

            if row_id in originals:
                raise InventoryCountHistoryError(
                    f"duplicate history id: {row_id}"
                )

            originals[row_id] = row
            continue

        reversal_of_id = _positive_id(
            row.reversal_of_id,
            field="reversal_of_id",
        )

        if reversal_of_id == row_id:
            raise InventoryCountHistoryError(
                "history row cannot reverse itself"
            )

        if reversal_of_id in reversed_ids:
            raise InventoryCountHistoryError(
                f"original {reversal_of_id} is reversed "
                f"more than once"
            )

        reversed_ids.add(reversal_of_id)

    unknown = reversed_ids - set(originals)

    if unknown:
        raise InventoryCountHistoryError(
            "reversal references unknown original ids "
            f"inside history chain: {sorted(unknown)}"
        )

    active = [
        row
        for row_id, row in originals.items()
        if row_id not in reversed_ids
    ]

    if len(active) > 1:
        raise InventoryCountHistoryError(
            "multiple ACTIVE inventory count originals "
            "inside one history chain"
        )

    if not active:
        return None

    return active[0]


def plan_inventory_count_history(
    *,
    history: Iterable[InventoryCountHistoryItem],
    history_key: str,
    target: InventoryCountTarget | None,
    adjustment_date: date | None = None,
) -> InventoryCountHistoryPlan:
    history_key = normalize_history_key(history_key)

    active = resolve_active_inventory_count_original(
        history,
        history_key=history_key,
    )

    if active is None:
        if target is None:
            return InventoryCountHistoryPlan(
                history_key=history_key,
                action=InventoryCountHistoryAction.NOOP,
                active_original=None,
                target=None,
                reversal_date=None,
            )

        return InventoryCountHistoryPlan(
            history_key=history_key,
            action=InventoryCountHistoryAction.CREATE,
            active_original=None,
            target=target,
            reversal_date=None,
        )

    if target == active.target:
        return InventoryCountHistoryPlan(
            history_key=history_key,
            action=InventoryCountHistoryAction.NOOP,
            active_original=active,
            target=target,
            reversal_date=None,
        )

    if adjustment_date is None:
        raise InventoryCountHistoryError(
            "adjustment_date is required when an ACTIVE "
            "count must be reversed"
        )

    if adjustment_date < active.target.count_date:
        raise InventoryCountHistoryError(
            "adjustment_date cannot predate active count"
        )

    if target is None:
        return InventoryCountHistoryPlan(
            history_key=history_key,
            action=InventoryCountHistoryAction.REVERSE,
            active_original=active,
            target=None,
            reversal_date=adjustment_date,
        )

    if target.count_date < active.target.count_date:
        raise InventoryCountHistoryError(
            "replacement count cannot move backward in time"
        )

    return InventoryCountHistoryPlan(
        history_key=history_key,
        action=(
            InventoryCountHistoryAction
            .REVERSE_AND_REPLACE
        ),
        active_original=active,
        target=target,
        reversal_date=adjustment_date,
    )
