from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


ZERO = Decimal("0")


class WarehouseTransferCalculationError(ValueError):
    pass


def _decimal(
    value: Decimal | int | str,
    *,
    field: str,
) -> Decimal:
    try:
        result = Decimal(value)
    except Exception as exc:
        raise WarehouseTransferCalculationError(
            f"{field} must be a valid decimal"
        ) from exc

    if not result.is_finite():
        raise WarehouseTransferCalculationError(
            f"{field} must be finite"
        )

    return result


def _positive_id(
    value: int,
    *,
    field: str,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise WarehouseTransferCalculationError(
            f"{field} must be an integer"
        )

    if value <= 0:
        raise WarehouseTransferCalculationError(
            f"{field} must be greater than zero"
        )

    return value


@dataclass(frozen=True)
class WarehouseTransferTarget:
    company_id: int
    product_id: int
    source_warehouse_id: int
    destination_warehouse_id: int
    quantity: Decimal

    @property
    def source_quantity_delta(self) -> Decimal:
        return -self.quantity

    @property
    def destination_quantity_delta(self) -> Decimal:
        return self.quantity


def calculate_warehouse_transfer_target(
    *,
    company_id: int,
    product_id: int,
    source_warehouse_id: int,
    destination_warehouse_id: int,
    quantity: Decimal,
    source_quantity_on_hand: Decimal,
) -> WarehouseTransferTarget:
    """
    Pure physical transfer calculation.

    This function deliberately does not calculate inventory value.
    Valuation is a separate FIFO / moving-average concern.

    Invariants:
    - one company/product;
    - two distinct warehouses;
    - positive transfer quantity;
    - source stock cannot become negative;
    - source/destination quantity deltas conserve quantity.
    """

    company_id = _positive_id(
        company_id,
        field="company_id",
    )
    product_id = _positive_id(
        product_id,
        field="product_id",
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
        raise WarehouseTransferCalculationError(
            "source and destination warehouses must differ"
        )

    quantity = _decimal(
        quantity,
        field="quantity",
    )
    source_quantity_on_hand = _decimal(
        source_quantity_on_hand,
        field="source_quantity_on_hand",
    )

    if quantity <= ZERO:
        raise WarehouseTransferCalculationError(
            "quantity must be greater than zero"
        )

    if source_quantity_on_hand < ZERO:
        raise WarehouseTransferCalculationError(
            "source_quantity_on_hand cannot be negative"
        )

    if quantity > source_quantity_on_hand:
        raise WarehouseTransferCalculationError(
            "transfer quantity exceeds source stock"
        )

    target = WarehouseTransferTarget(
        company_id=company_id,
        product_id=product_id,
        source_warehouse_id=source_warehouse_id,
        destination_warehouse_id=destination_warehouse_id,
        quantity=quantity,
    )

    if (
        target.source_quantity_delta
        + target.destination_quantity_delta
        != ZERO
    ):
        raise WarehouseTransferCalculationError(
            "transfer quantity conservation failed"
        )

    return target
