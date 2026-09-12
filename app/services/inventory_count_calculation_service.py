from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum


ZERO = Decimal("0")


class InventoryCountCalculationError(ValueError):
    pass


class InventoryCountVarianceDirection(str, Enum):
    NONE = "none"
    INCREASE = "increase"
    DECREASE = "decrease"


def _decimal(
    value: Decimal | int | str,
    *,
    field: str,
) -> Decimal:
    try:
        result = Decimal(value)
    except Exception as exc:
        raise InventoryCountCalculationError(
            f"{field} must be a valid decimal"
        ) from exc

    if not result.is_finite():
        raise InventoryCountCalculationError(
            f"{field} must be finite"
        )

    return result


def _positive_id(
    value: int,
    *,
    field: str,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise InventoryCountCalculationError(
            f"{field} must be an integer"
        )

    if value <= 0:
        raise InventoryCountCalculationError(
            f"{field} must be greater than zero"
        )

    return value


@dataclass(frozen=True)
class InventoryCountVarianceTarget:
    company_id: int
    product_id: int
    warehouse_id: int
    expected_quantity: Decimal
    counted_quantity: Decimal
    variance_quantity: Decimal
    direction: InventoryCountVarianceDirection

    @property
    def requires_stock_adjustment(self) -> bool:
        return self.variance_quantity != ZERO


def calculate_inventory_count_variance(
    *,
    company_id: int,
    product_id: int,
    warehouse_id: int,
    expected_quantity: Decimal,
    counted_quantity: Decimal,
) -> InventoryCountVarianceTarget:
    """
    Pure inventory-count calculation.

    A count is an observation. The variance is the economic/physical
    correction target.

    No valuation is invented here. Positive FIFO/MA count variances
    require an explicit valuation policy in a later layer.
    """

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

    expected_quantity = _decimal(
        expected_quantity,
        field="expected_quantity",
    )
    counted_quantity = _decimal(
        counted_quantity,
        field="counted_quantity",
    )

    if expected_quantity < ZERO:
        raise InventoryCountCalculationError(
            "expected_quantity cannot be negative"
        )

    if counted_quantity < ZERO:
        raise InventoryCountCalculationError(
            "counted_quantity cannot be negative"
        )

    variance = counted_quantity - expected_quantity

    if variance > ZERO:
        direction = InventoryCountVarianceDirection.INCREASE
    elif variance < ZERO:
        direction = InventoryCountVarianceDirection.DECREASE
    else:
        direction = InventoryCountVarianceDirection.NONE

    return InventoryCountVarianceTarget(
        company_id=company_id,
        product_id=product_id,
        warehouse_id=warehouse_id,
        expected_quantity=expected_quantity,
        counted_quantity=counted_quantity,
        variance_quantity=variance,
        direction=direction,
    )
