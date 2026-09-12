from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.company import InventoryValuationMethod
from app.models.document_line import DocumentLine
from app.models.inventory_cost_entry import InventoryCostEntry
from app.models.warehouse_transfer_valuation_layer import (
    WarehouseTransferValuationLayer,
)


ZERO = Decimal("0")


class PurchaseValueCorrectionMovingAverageTransferRoutingError(
    Exception
):
    """Base MA transfer-routing error."""


class PurchaseValueCorrectionMovingAverageTransferRoutingIntegrityError(
    PurchaseValueCorrectionMovingAverageTransferRoutingError
):
    """Immutable transfer provenance is internally inconsistent."""


class PurchaseValueCorrectionMovingAverageTransferRoutingAmbiguityError(
    PurchaseValueCorrectionMovingAverageTransferRoutingError
):
    """One source ICE resolves to ambiguous MA transfer provenance."""


class PurchaseValueCorrectionMovingAverageTransferRoutingNotFoundError(
    PurchaseValueCorrectionMovingAverageTransferRoutingError
):
    """Required routed destination provenance is missing."""


@dataclass(
    frozen=True,
    slots=True,
)
class PurchaseValueCorrectionMovingAverageTransferRoute:
    """
    Exact immutable routing bridge:

        source transfer ISSUE InventoryCostEntry
            -> WarehouseTransferValuationLayer
            -> destination transfer RECEIPT DocumentLine
            -> destination transfer RECEIPT InventoryCostEntry

    This object contains provenance only.

    It does not:
      * calculate moving-average economics;
      * mutate historical rows;
      * create replay events;
      * post accounting;
      * own a transaction.
    """

    warehouse_transfer_valuation_layer_id: int
    company_id: int
    product_id: int
    destination_warehouse_id: int

    source_inventory_cost_entry_id: int

    destination_receipt_document_id: int
    destination_receipt_document_line_id: int
    destination_inventory_cost_entry_id: int

    quantity: Decimal
    unit_cost: Decimal
    valuation_amount: Decimal


def _positive_id(
    value,
    *,
    field: str,
) -> int:
    if (
        not isinstance(
            value,
            int,
        )
        or isinstance(
            value,
            bool,
        )
        or value <= 0
    ):
        raise (
            PurchaseValueCorrectionMovingAverageTransferRoutingIntegrityError(
                f"{field} must be a positive integer"
            )
        )

    return value


def _decimal(
    value,
    *,
    field: str,
) -> Decimal:
    try:
        result = Decimal(
            value
        )
    except Exception as exc:
        raise (
            PurchaseValueCorrectionMovingAverageTransferRoutingIntegrityError(
                f"{field} must be a finite decimal"
            )
        ) from exc

    if not result.is_finite():
        raise (
            PurchaseValueCorrectionMovingAverageTransferRoutingIntegrityError(
                f"{field} must be finite"
            )
        )

    return result


def _valuation_method_value(
    value: Any,
) -> str:
    raw = getattr(
        value,
        "value",
        value,
    )

    return str(
        raw
    ).strip().lower()


def build_moving_average_transfer_route_from_rows(
    *,
    layer: WarehouseTransferValuationLayer,
    destination_line: DocumentLine,
    destination_inventory_cost_entry: InventoryCostEntry,
) -> PurchaseValueCorrectionMovingAverageTransferRoute:
    """
    Validate exact immutable MA transfer provenance.

    This is intentionally PURE.

    Authoritative monetary values come from immutable WTVL /
    InventoryCostEntry Q8 fields. DocumentLine.price is not used.
    """

    if (
        _valuation_method_value(
            layer.valuation_method
        )
        != InventoryValuationMethod.WEIGHTED_AVERAGE_MOVING.value
    ):
        raise (
            PurchaseValueCorrectionMovingAverageTransferRoutingIntegrityError(
                "Warehouse transfer valuation layer is not moving average"
            )
        )

    if (
        layer.source_stock_lot_consumption_id
        is not None
    ):
        raise (
            PurchaseValueCorrectionMovingAverageTransferRoutingIntegrityError(
                "Moving-average transfer layer must not reference "
                "StockLotConsumption"
            )
        )

    layer_id = _positive_id(
        layer.id,
        field="WarehouseTransferValuationLayer.id",
    )

    company_id = _positive_id(
        layer.company_id,
        field="company_id",
    )

    product_id = _positive_id(
        layer.product_id,
        field="product_id",
    )

    destination_warehouse_id = _positive_id(
        layer.destination_warehouse_id,
        field="destination_warehouse_id",
    )

    source_ice_id = _positive_id(
        layer.source_inventory_cost_entry_id,
        field="source_inventory_cost_entry_id",
    )

    destination_document_id = _positive_id(
        layer.destination_receipt_document_id,
        field="destination_receipt_document_id",
    )

    destination_line_id = _positive_id(
        layer.destination_receipt_document_line_id,
        field="destination_receipt_document_line_id",
    )

    line_id = _positive_id(
        destination_line.id,
        field="destination DocumentLine.id",
    )

    line_document_id = _positive_id(
        destination_line.document_id,
        field="destination DocumentLine.document_id",
    )

    line_product_id = _positive_id(
        destination_line.product_id,
        field="destination DocumentLine.product_id",
    )

    line_warehouse_id = _positive_id(
        destination_line.warehouse_id,
        field="destination DocumentLine.warehouse_id",
    )

    if (
        line_id != destination_line_id
        or line_document_id != destination_document_id
    ):
        raise (
            PurchaseValueCorrectionMovingAverageTransferRoutingIntegrityError(
                "Destination DocumentLine does not match WTVL provenance"
            )
        )

    if (
        line_product_id != product_id
        or line_warehouse_id != destination_warehouse_id
    ):
        raise (
            PurchaseValueCorrectionMovingAverageTransferRoutingIntegrityError(
                "Destination DocumentLine product/warehouse does not "
                "match WTVL provenance"
            )
        )

    destination_ice_id = _positive_id(
        destination_inventory_cost_entry.id,
        field="destination InventoryCostEntry.id",
    )

    if (
        _positive_id(
            destination_inventory_cost_entry.company_id,
            field="destination InventoryCostEntry.company_id",
        )
        != company_id
    ):
        raise (
            PurchaseValueCorrectionMovingAverageTransferRoutingIntegrityError(
                "Destination InventoryCostEntry belongs to another company"
            )
        )

    if (
        _positive_id(
            destination_inventory_cost_entry.document_id,
            field="destination InventoryCostEntry.document_id",
        )
        != destination_document_id
        or _positive_id(
            destination_inventory_cost_entry.document_line_id,
            field="destination InventoryCostEntry.document_line_id",
        )
        != destination_line_id
    ):
        raise (
            PurchaseValueCorrectionMovingAverageTransferRoutingIntegrityError(
                "Destination InventoryCostEntry does not match "
                "WTVL destination receipt provenance"
            )
        )

    if (
        _valuation_method_value(
            destination_inventory_cost_entry.valuation_method
        )
        != InventoryValuationMethod.WEIGHTED_AVERAGE_MOVING.value
    ):
        raise (
            PurchaseValueCorrectionMovingAverageTransferRoutingIntegrityError(
                "Destination InventoryCostEntry is not moving average"
            )
        )

    layer_quantity = _decimal(
        layer.quantity,
        field="WTVL.quantity",
    )

    layer_unit_cost = _decimal(
        layer.unit_cost,
        field="WTVL.unit_cost",
    )

    layer_value = _decimal(
        layer.valuation_amount,
        field="WTVL.valuation_amount",
    )

    ice_quantity = _decimal(
        destination_inventory_cost_entry.quantity,
        field="destination ICE.quantity",
    )

    ice_unit_cost = _decimal(
        destination_inventory_cost_entry.unit_cost,
        field="destination ICE.unit_cost",
    )

    ice_value = _decimal(
        destination_inventory_cost_entry.valuation_amount,
        field="destination ICE.valuation_amount",
    )

    if layer_quantity <= ZERO:
        raise (
            PurchaseValueCorrectionMovingAverageTransferRoutingIntegrityError(
                "WTVL quantity must be positive"
            )
        )

    if (
        layer_unit_cost < ZERO
        or layer_value < ZERO
        or ice_unit_cost < ZERO
        or ice_value < ZERO
    ):
        raise (
            PurchaseValueCorrectionMovingAverageTransferRoutingIntegrityError(
                "Moving-average transfer valuation cannot be negative"
            )
        )

    if ice_quantity != layer_quantity:
        raise (
            PurchaseValueCorrectionMovingAverageTransferRoutingIntegrityError(
                "Destination InventoryCostEntry quantity differs from "
                "immutable WTVL quantity"
            )
        )

    if ice_unit_cost != layer_unit_cost:
        raise (
            PurchaseValueCorrectionMovingAverageTransferRoutingIntegrityError(
                "Destination InventoryCostEntry unit cost differs from "
                "immutable WTVL Q8 unit cost"
            )
        )

    if ice_value != layer_value:
        raise (
            PurchaseValueCorrectionMovingAverageTransferRoutingIntegrityError(
                "Destination InventoryCostEntry valuation amount differs "
                "from immutable WTVL Q8 valuation amount"
            )
        )

    return PurchaseValueCorrectionMovingAverageTransferRoute(
        warehouse_transfer_valuation_layer_id=layer_id,
        company_id=company_id,
        product_id=product_id,
        destination_warehouse_id=destination_warehouse_id,
        source_inventory_cost_entry_id=source_ice_id,
        destination_receipt_document_id=destination_document_id,
        destination_receipt_document_line_id=destination_line_id,
        destination_inventory_cost_entry_id=destination_ice_id,
        quantity=layer_quantity,
        unit_cost=layer_unit_cost,
        valuation_amount=layer_value,
    )


async def resolve_moving_average_transfer_route(
    db: AsyncSession,
    *,
    company_id: int,
    source_inventory_cost_entry_id: int,
) -> (
    PurchaseValueCorrectionMovingAverageTransferRoute
    | None
):
    """
    Resolve one source MA ISSUE ICE across an immutable warehouse
    transfer boundary.

    Non-transfer source ICE:
        return None.

    Transfer source ICE:
        require exactly one MA WTVL and exact destination
        receipt DocumentLine + InventoryCostEntry provenance.

    Caller owns transaction.
    """

    company_id = _positive_id(
        company_id,
        field="company_id",
    )

    source_inventory_cost_entry_id = _positive_id(
        source_inventory_cost_entry_id,
        field="source_inventory_cost_entry_id",
    )

    layers = tuple(
        (
            await db.execute(
                select(
                    WarehouseTransferValuationLayer
                )
                .where(
                    WarehouseTransferValuationLayer.company_id
                    == company_id,
                    WarehouseTransferValuationLayer.source_inventory_cost_entry_id
                    == source_inventory_cost_entry_id,
                )
                .order_by(
                    WarehouseTransferValuationLayer.id
                )
            )
        ).scalars().all()
    )

    if not layers:
        return None

    ma_layers = tuple(
        layer
        for layer in layers
        if (
            _valuation_method_value(
                layer.valuation_method
            )
            == (
                InventoryValuationMethod
                .WEIGHTED_AVERAGE_MOVING
                .value
            )
        )
    )

    if not ma_layers:
        return None

    if len(ma_layers) != 1:
        raise (
            PurchaseValueCorrectionMovingAverageTransferRoutingAmbiguityError(
                "Source InventoryCostEntry resolves to multiple "
                "moving-average transfer valuation layers"
            )
        )

    if len(layers) != 1:
        raise (
            PurchaseValueCorrectionMovingAverageTransferRoutingIntegrityError(
                "Source InventoryCostEntry is shared by mixed "
                "transfer valuation provenance"
            )
        )

    layer = ma_layers[0]

    destination_line = (
        await db.execute(
            select(
                DocumentLine
            ).where(
                DocumentLine.id
                == layer.destination_receipt_document_line_id,
                DocumentLine.document_id
                == layer.destination_receipt_document_id,
            )
        )
    ).scalar_one_or_none()

    if destination_line is None:
        raise (
            PurchaseValueCorrectionMovingAverageTransferRoutingNotFoundError(
                "Destination transfer receipt DocumentLine was not found"
            )
        )

    destination_ice = (
        await db.execute(
            select(
                InventoryCostEntry
            ).where(
                InventoryCostEntry.company_id
                == company_id,
                InventoryCostEntry.document_id
                == layer.destination_receipt_document_id,
                InventoryCostEntry.document_line_id
                == layer.destination_receipt_document_line_id,
                InventoryCostEntry.valuation_method
                == (
                    InventoryValuationMethod
                    .WEIGHTED_AVERAGE_MOVING
                ),
            )
        )
    ).scalar_one_or_none()

    if destination_ice is None:
        raise (
            PurchaseValueCorrectionMovingAverageTransferRoutingNotFoundError(
                "Destination transfer receipt moving-average "
                "InventoryCostEntry was not found"
            )
        )

    return build_moving_average_transfer_route_from_rows(
        layer=layer,
        destination_line=destination_line,
        destination_inventory_cost_entry=destination_ice,
    )
