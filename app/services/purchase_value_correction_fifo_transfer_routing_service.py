from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.warehouse_transfer_valuation_layer import (
    WarehouseTransferValuationLayer,
)


ZERO = Decimal("0")


class PurchaseValueCorrectionFifoTransferRoutingError(
    Exception
):
    """Base FIFO transfer-routing failure."""


class PurchaseValueCorrectionFifoTransferRoutingIntegrityError(
    PurchaseValueCorrectionFifoTransferRoutingError
):
    """Stored transfer provenance is inconsistent."""


@dataclass(
    frozen=True,
    slots=True,
)
class PurchaseValueCorrectionFifoTransferRoute:
    """
    Immutable read-only bridge from one source FIFO
    StockLotConsumption to its warehouse-transfer
    destination receipt layer.

    This object is provenance only.

    It does NOT:
      * mutate StockLot;
      * mutate StockLotConsumption;
      * mutate InventoryCostEntry;
      * mutate WarehouseTransferValuationLayer;
      * create PVC FIFO impacts;
      * create JournalEntry;
      * commit or rollback.
    """

    warehouse_transfer_valuation_layer_id: int

    company_id: int
    transfer_line_id: int
    product_id: int
    destination_warehouse_id: int

    source_inventory_cost_entry_id: int
    source_stock_lot_consumption_id: int

    destination_receipt_document_id: int
    destination_receipt_document_line_id: int

    quantity: Decimal
    unit_cost: Decimal
    valuation_amount: Decimal


def _positive_id(
    value,
    *,
    field: str,
) -> int:
    if (
        isinstance(
            value,
            bool,
        )
        or not isinstance(
            value,
            int,
        )
        or value <= 0
    ):
        raise (
            PurchaseValueCorrectionFifoTransferRoutingIntegrityError(
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
            str(value)
        )
    except (
        InvalidOperation,
        ValueError,
        TypeError,
    ) as exc:
        raise (
            PurchaseValueCorrectionFifoTransferRoutingIntegrityError(
                f"{field} must be Decimal-compatible"
            )
        ) from exc

    if not result.is_finite():
        raise (
            PurchaseValueCorrectionFifoTransferRoutingIntegrityError(
                f"{field} must be finite"
            )
        )

    return result


def _valuation_method_value(
    value,
) -> str:
    raw = getattr(
        value,
        "value",
        value,
    )

    return str(
        raw
    ).strip().lower()


def build_fifo_transfer_route_from_layer(
    layer: WarehouseTransferValuationLayer,
) -> PurchaseValueCorrectionFifoTransferRoute:
    """
    Validate one immutable WTVL row as FIFO transfer
    provenance and project it into the PVC routing
    contract.
    """

    valuation_method = _valuation_method_value(
        layer.valuation_method
    )

    if valuation_method != "fifo":
        raise (
            PurchaseValueCorrectionFifoTransferRoutingIntegrityError(
                "Warehouse transfer valuation layer is not FIFO"
            )
        )

    layer_id = _positive_id(
        layer.id,
        field=(
            "WarehouseTransferValuationLayer.id"
        ),
    )

    company_id = _positive_id(
        layer.company_id,
        field="company_id",
    )

    transfer_line_id = _positive_id(
        layer.transfer_line_id,
        field="transfer_line_id",
    )

    product_id = _positive_id(
        layer.product_id,
        field="product_id",
    )

    destination_warehouse_id = _positive_id(
        layer.destination_warehouse_id,
        field="destination_warehouse_id",
    )

    source_inventory_cost_entry_id = _positive_id(
        layer.source_inventory_cost_entry_id,
        field="source InventoryCostEntry",
    )

    if (
        layer.source_stock_lot_consumption_id
        is None
    ):
        raise (
            PurchaseValueCorrectionFifoTransferRoutingIntegrityError(
                "FIFO transfer layer must reference "
                "source StockLotConsumption"
            )
        )

    source_stock_lot_consumption_id = _positive_id(
        layer.source_stock_lot_consumption_id,
        field="source StockLotConsumption",
    )

    destination_receipt_document_id = _positive_id(
        layer.destination_receipt_document_id,
        field="destination receipt document",
    )

    destination_receipt_document_line_id = _positive_id(
        layer.destination_receipt_document_line_id,
        field="destination receipt document line",
    )

    quantity = _decimal(
        layer.quantity,
        field="quantity",
    )

    if quantity <= ZERO:
        raise (
            PurchaseValueCorrectionFifoTransferRoutingIntegrityError(
                "quantity must be greater than zero"
            )
        )

    unit_cost = _decimal(
        layer.unit_cost,
        field="unit_cost",
    )

    if unit_cost < ZERO:
        raise (
            PurchaseValueCorrectionFifoTransferRoutingIntegrityError(
                "unit_cost must be nonnegative"
            )
        )

    valuation_amount = _decimal(
        layer.valuation_amount,
        field="valuation_amount",
    )

    if valuation_amount < ZERO:
        raise (
            PurchaseValueCorrectionFifoTransferRoutingIntegrityError(
                "valuation_amount must be nonnegative"
            )
        )

    return PurchaseValueCorrectionFifoTransferRoute(
        warehouse_transfer_valuation_layer_id=layer_id,
        company_id=company_id,
        transfer_line_id=transfer_line_id,
        product_id=product_id,
        destination_warehouse_id=(
            destination_warehouse_id
        ),
        source_inventory_cost_entry_id=(
            source_inventory_cost_entry_id
        ),
        source_stock_lot_consumption_id=(
            source_stock_lot_consumption_id
        ),
        destination_receipt_document_id=(
            destination_receipt_document_id
        ),
        destination_receipt_document_line_id=(
            destination_receipt_document_line_id
        ),
        quantity=quantity,
        unit_cost=unit_cost,
        valuation_amount=valuation_amount,
    )


async def load_fifo_transfer_route_for_consumption(
    db: AsyncSession,
    *,
    company_id: int,
    stock_lot_consumption_id: int,
) -> (
    PurchaseValueCorrectionFifoTransferRoute
    | None
):
    """
    Resolve whether one historical FIFO
    StockLotConsumption is a source warehouse-transfer
    ISSUE layer.

    None means:
        this consumption has no WTVL transfer provenance.

    A route means:
        the source consumption is an immutable transfer
        routing boundary and its destination receipt
        provenance is known.

    Lifecycle/economic classification of the destination
    is deliberately outside this loader.
    """

    company_id = _positive_id(
        company_id,
        field="company_id",
    )

    stock_lot_consumption_id = _positive_id(
        stock_lot_consumption_id,
        field="stock_lot_consumption_id",
    )

    statement = (
        select(
            WarehouseTransferValuationLayer
        )
        .where(
            WarehouseTransferValuationLayer.company_id
            == company_id,
            (
                WarehouseTransferValuationLayer
                .source_stock_lot_consumption_id
                == stock_lot_consumption_id
            ),
        )
        .order_by(
            WarehouseTransferValuationLayer.id
        )
    )

    rows = tuple(
        (
            await db.execute(
                statement
            )
        ).scalars().all()
    )

    if not rows:
        return None

    if len(rows) != 1:
        raise (
            PurchaseValueCorrectionFifoTransferRoutingIntegrityError(
                "FIFO StockLotConsumption has multiple "
                "warehouse-transfer valuation layers"
            )
        )

    route = build_fifo_transfer_route_from_layer(
        rows[0]
    )

    if route.company_id != company_id:
        raise (
            PurchaseValueCorrectionFifoTransferRoutingIntegrityError(
                "Warehouse transfer route company mismatch"
            )
        )

    if (
        route.source_stock_lot_consumption_id
        != stock_lot_consumption_id
    ):
        raise (
            PurchaseValueCorrectionFifoTransferRoutingIntegrityError(
                "Warehouse transfer route "
                "StockLotConsumption mismatch"
            )
        )

    return route
