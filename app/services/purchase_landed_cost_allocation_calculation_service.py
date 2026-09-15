from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP


MONEY_QUANTUM = Decimal("0.01")


class PurchaseLandedCostAllocationCalculationError(Exception):
    """Invalid landed-cost allocation input."""


@dataclass(frozen=True)
class PurchaseLandedCostAllocationInput:
    fulfillment_line_id: int
    quantity: Decimal


@dataclass(frozen=True)
class PurchaseLandedCostAllocation:
    fulfillment_line_id: int
    quantity: Decimal
    allocated_amount: Decimal


def _money(value: Decimal) -> Decimal:
    return Decimal(value).quantize(
        MONEY_QUANTUM,
        rounding=ROUND_HALF_UP,
    )


def calculate_purchase_landed_cost_allocations(
    *,
    total_amount: Decimal,
    lines: tuple[PurchaseLandedCostAllocationInput, ...],
) -> tuple[PurchaseLandedCostAllocation, ...]:
    """
    Allocate landed cost proportionally by fulfilled quantity.

    V1 deliberately supports one deterministic allocation basis:
    receipt quantity.

    Monetary rounding remainder is assigned to the final line so the
    persisted allocations always reconcile exactly to total_amount.
    """

    amount = _money(total_amount)

    if amount <= 0:
        raise PurchaseLandedCostAllocationCalculationError(
            "total_amount must be positive"
        )

    if not lines:
        raise PurchaseLandedCostAllocationCalculationError(
            "at least one fulfillment line is required"
        )

    seen: set[int] = set()
    total_quantity = Decimal("0")

    for line in lines:
        if line.fulfillment_line_id <= 0:
            raise PurchaseLandedCostAllocationCalculationError(
                "fulfillment_line_id must be positive"
            )

        if line.fulfillment_line_id in seen:
            raise PurchaseLandedCostAllocationCalculationError(
                "duplicate fulfillment_line_id"
            )
        seen.add(line.fulfillment_line_id)

        quantity = Decimal(line.quantity)
        if quantity <= 0:
            raise PurchaseLandedCostAllocationCalculationError(
                "quantity must be positive"
            )

        total_quantity += quantity

    allocated_so_far = Decimal("0.00")
    result: list[PurchaseLandedCostAllocation] = []

    for index, line in enumerate(lines):
        quantity = Decimal(line.quantity)

        if index == len(lines) - 1:
            allocated = amount - allocated_so_far
        else:
            allocated = _money(
                amount * quantity / total_quantity
            )
            allocated_so_far += allocated

        if allocated <= 0:
            raise PurchaseLandedCostAllocationCalculationError(
                "allocation produced a non-positive amount"
            )

        result.append(
            PurchaseLandedCostAllocation(
                fulfillment_line_id=line.fulfillment_line_id,
                quantity=quantity,
                allocated_amount=allocated,
            )
        )

    if sum(
        item.allocated_amount
        for item in result
    ) != amount:
        raise PurchaseLandedCostAllocationCalculationError(
            "allocations do not reconcile to total_amount"
        )

    return tuple(result)
