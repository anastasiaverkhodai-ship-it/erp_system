from __future__ import annotations

from dataclasses import dataclass
from decimal import (
    Decimal,
    ROUND_HALF_UP,
)

from app.models.company import InventoryValuationMethod


class WarehouseTransferValuationCalculationError(
    ValueError
):
    pass


DOCUMENT_PRICE_QUANTUM = Decimal("0.0001")


@dataclass(frozen=True)
class WarehouseTransferSourceInventoryCost:
    """
    Immutable projection of the source ISSUE InventoryCostEntry.

    This calculator intentionally accepts values rather than ORM rows,
    so the calculation itself is pure.
    """

    inventory_cost_entry_id: int
    valuation_method: InventoryValuationMethod
    quantity: Decimal
    unit_cost: Decimal
    valuation_amount: Decimal


@dataclass(frozen=True)
class WarehouseTransferFifoConsumptionSlice:
    """
    Immutable projection of one source StockLotConsumption.
    """

    stock_lot_consumption_id: int
    quantity: Decimal
    unit_cost: Decimal


@dataclass(frozen=True)
class WarehouseTransferValuationTargetLayer:
    """
    One exact transfer valuation layer before destination receipt
    DocumentLines exist.

    FIFO:
        source_stock_lot_consumption_id is required.

    Moving average:
        source_stock_lot_consumption_id is None.
    """

    valuation_method: InventoryValuationMethod
    quantity: Decimal
    unit_cost: Decimal
    valuation_amount: Decimal
    source_inventory_cost_entry_id: int
    source_stock_lot_consumption_id: int | None


@dataclass(frozen=True)
class WarehouseTransferValuationResult:
    valuation_method: InventoryValuationMethod
    source_inventory_cost_entry_id: int
    source_quantity: Decimal
    source_valuation_amount: Decimal
    layers: tuple[
        WarehouseTransferValuationTargetLayer,
        ...
    ]

    @property
    def layer_quantity(self) -> Decimal:
        return sum(
            (
                layer.quantity
                for layer in self.layers
            ),
            Decimal("0"),
        )

    @property
    def layer_valuation_amount(self) -> Decimal:
        return sum(
            (
                layer.valuation_amount
                for layer in self.layers
            ),
            Decimal("0"),
        )

    @property
    def requires_exact_receipt_value_path(self) -> bool:
        """
        Existing DocumentLine.price has 4 decimal places.

        FIFO source StockLotConsumption.unit_cost also has 4 decimal
        places, so its cost layers can naturally pass through ordinary
        receipt DocumentLines.

        Moving-average ICE unit_cost has 8 decimal places. Quantizing
        that cost to DocumentLine.price precision may change the
        transferred valuation amount. If so, the future physical
        factory must NOT silently use only DocumentLine.price.
        """

        if (
            self.valuation_method
            != InventoryValuationMethod.WEIGHTED_AVERAGE_MOVING
        ):
            return False

        layer = self.layers[0]

        receipt_price = layer.unit_cost.quantize(
            DOCUMENT_PRICE_QUANTUM,
            rounding=ROUND_HALF_UP,
        )

        projected_value = (
            layer.quantity
            * receipt_price
        )

        return (
            projected_value
            != layer.valuation_amount
        )


def _positive_id(
    value: int,
    *,
    field: str,
) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value <= 0
    ):
        raise WarehouseTransferValuationCalculationError(
            f"{field} must be a positive integer"
        )

    return value


def _decimal(
    value,
    *,
    field: str,
) -> Decimal:
    try:
        result = Decimal(value)
    except Exception as exc:
        raise WarehouseTransferValuationCalculationError(
            f"{field} must be a valid decimal"
        ) from exc

    if not result.is_finite():
        raise WarehouseTransferValuationCalculationError(
            f"{field} must be finite"
        )

    return result


def _normalize_source(
    source: WarehouseTransferSourceInventoryCost,
) -> WarehouseTransferSourceInventoryCost:
    inventory_cost_entry_id = _positive_id(
        source.inventory_cost_entry_id,
        field="inventory_cost_entry_id",
    )

    quantity = _decimal(
        source.quantity,
        field="source quantity",
    )

    unit_cost = _decimal(
        source.unit_cost,
        field="source unit_cost",
    )

    valuation_amount = _decimal(
        source.valuation_amount,
        field="source valuation_amount",
    )

    if quantity <= 0:
        raise WarehouseTransferValuationCalculationError(
            "source quantity must be positive"
        )

    if unit_cost < 0:
        raise WarehouseTransferValuationCalculationError(
            "source unit_cost cannot be negative"
        )

    if valuation_amount < 0:
        raise WarehouseTransferValuationCalculationError(
            "source valuation_amount cannot be negative"
        )

    if source.valuation_method not in {
        InventoryValuationMethod.FIFO,
        InventoryValuationMethod.WEIGHTED_AVERAGE_MOVING,
    }:
        raise WarehouseTransferValuationCalculationError(
            "unsupported inventory valuation method"
        )

    return WarehouseTransferSourceInventoryCost(
        inventory_cost_entry_id=inventory_cost_entry_id,
        valuation_method=source.valuation_method,
        quantity=quantity,
        unit_cost=unit_cost,
        valuation_amount=valuation_amount,
    )


def _build_fifo_layers(
    *,
    source: WarehouseTransferSourceInventoryCost,
    fifo_consumptions: tuple[
        WarehouseTransferFifoConsumptionSlice,
        ...
    ],
) -> tuple[
    WarehouseTransferValuationTargetLayer,
    ...
]:
    if not fifo_consumptions:
        raise WarehouseTransferValuationCalculationError(
            "FIFO transfer requires source "
            "StockLotConsumption slices"
        )

    layers = []
    seen_ids = set()

    for slice_ in fifo_consumptions:
        consumption_id = _positive_id(
            slice_.stock_lot_consumption_id,
            field="stock_lot_consumption_id",
        )

        if consumption_id in seen_ids:
            raise WarehouseTransferValuationCalculationError(
                "duplicate source StockLotConsumption id"
            )

        seen_ids.add(
            consumption_id
        )

        quantity = _decimal(
            slice_.quantity,
            field="FIFO consumption quantity",
        )

        unit_cost = _decimal(
            slice_.unit_cost,
            field="FIFO consumption unit_cost",
        )

        if quantity <= 0:
            raise WarehouseTransferValuationCalculationError(
                "FIFO consumption quantity must be positive"
            )

        if unit_cost < 0:
            raise WarehouseTransferValuationCalculationError(
                "FIFO consumption unit_cost cannot be negative"
            )

        # StockLotConsumption.quantity and unit_cost both have
        # scale 4, therefore multiplication has at most scale 8.
        # No averaging and no new cost derivation.
        valuation_amount = (
            quantity
            * unit_cost
        )

        layers.append(
            WarehouseTransferValuationTargetLayer(
                valuation_method=(
                    InventoryValuationMethod.FIFO
                ),
                quantity=quantity,
                unit_cost=unit_cost,
                valuation_amount=valuation_amount,
                source_inventory_cost_entry_id=(
                    source.inventory_cost_entry_id
                ),
                source_stock_lot_consumption_id=(
                    consumption_id
                ),
            )
        )

    result = tuple(
        layers
    )

    quantity_total = sum(
        (
            layer.quantity
            for layer in result
        ),
        Decimal("0"),
    )

    if quantity_total != source.quantity:
        raise WarehouseTransferValuationCalculationError(
            "FIFO consumption quantities do not equal "
            "source InventoryCostEntry quantity"
        )

    value_total = sum(
        (
            layer.valuation_amount
            for layer in result
        ),
        Decimal("0"),
    )

    if value_total != source.valuation_amount:
        raise WarehouseTransferValuationCalculationError(
            "FIFO consumption valuation does not equal "
            "source InventoryCostEntry valuation_amount"
        )

    return result


def _build_moving_average_layers(
    *,
    source: WarehouseTransferSourceInventoryCost,
    fifo_consumptions: tuple[
        WarehouseTransferFifoConsumptionSlice,
        ...
    ],
) -> tuple[
    WarehouseTransferValuationTargetLayer,
    ...
]:
    if fifo_consumptions:
        raise WarehouseTransferValuationCalculationError(
            "moving-average transfer must not contain "
            "FIFO consumption slices"
        )

    # InventoryCostEntry is the immutable exact source ISSUE
    # valuation truth.
    #
    # Do NOT recompute valuation_amount as quantity * unit_cost.
    # ICE.unit_cost is an effective unit cost rounded to scale 8,
    # while ICE.valuation_amount is independently retained at scale 8.
    return (
        WarehouseTransferValuationTargetLayer(
            valuation_method=(
                InventoryValuationMethod
                .WEIGHTED_AVERAGE_MOVING
            ),
            quantity=source.quantity,
            unit_cost=source.unit_cost,
            valuation_amount=source.valuation_amount,
            source_inventory_cost_entry_id=(
                source.inventory_cost_entry_id
            ),
            source_stock_lot_consumption_id=None,
        ),
    )


def calculate_warehouse_transfer_valuation(
    *,
    source: WarehouseTransferSourceInventoryCost,
    fifo_consumptions: tuple[
        WarehouseTransferFifoConsumptionSlice,
        ...
    ] = (),
) -> WarehouseTransferValuationResult:
    """
    Pure transfer valuation calculation.

    No ORM mutation.
    No database access.
    No commit / rollback.

    FIFO:
        StockLotConsumption slices are the layer truth.
        Quantity and value must conserve exactly to source ICE.

    Moving average:
        source ICE is the exact valuation truth.
        One layer is emitted.
    """

    source = _normalize_source(
        source
    )

    fifo_consumptions = tuple(
        fifo_consumptions
    )

    if (
        source.valuation_method
        == InventoryValuationMethod.FIFO
    ):
        layers = _build_fifo_layers(
            source=source,
            fifo_consumptions=fifo_consumptions,
        )

    elif (
        source.valuation_method
        == InventoryValuationMethod.WEIGHTED_AVERAGE_MOVING
    ):
        layers = _build_moving_average_layers(
            source=source,
            fifo_consumptions=fifo_consumptions,
        )

    else:
        raise WarehouseTransferValuationCalculationError(
            "unsupported inventory valuation method"
        )

    result = WarehouseTransferValuationResult(
        valuation_method=source.valuation_method,
        source_inventory_cost_entry_id=(
            source.inventory_cost_entry_id
        ),
        source_quantity=source.quantity,
        source_valuation_amount=(
            source.valuation_amount
        ),
        layers=layers,
    )

    if (
        result.layer_quantity
        != result.source_quantity
    ):
        raise WarehouseTransferValuationCalculationError(
            "transfer valuation quantity conservation failed"
        )

    if (
        result.layer_valuation_amount
        != result.source_valuation_amount
    ):
        raise WarehouseTransferValuationCalculationError(
            "transfer valuation value conservation failed"
        )

    return result
