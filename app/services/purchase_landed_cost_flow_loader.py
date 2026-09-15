"""Read existing stock provenance into a value-allocation graph."""
from collections import defaultdict
from datetime import date
from decimal import Decimal, localcontext

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document
from app.models.document_line import DocumentLine
from app.models.inventory_cost_entry import InventoryCostEntry
from app.models.moving_average_movement import MovingAverageMovement
from app.models.sales_return_cost_restoration_event import SalesReturnCostRestorationEvent
from app.models.sales_return_cost_restoration_fifo_slice import SalesReturnCostRestorationFifoSlice
from app.models.stock_lot import StockLot
from app.models.stock_lot_consumption import StockLotConsumption
from app.models.trade_return_event import TradeReturnEvent
from app.models.warehouse_transfer_valuation_layer import WarehouseTransferValuationLayer
from app.services.purchase_landed_cost_flow_service import (
    CostDestination, CostFlowEdge, PurchaseLandedCostFlowError,
)


ZERO = Decimal(0)
ONE = Decimal(1)


def _active(rows):
    reversed_ids = {r.reversal_of_id for r in rows if r.reversal_of_id is not None}
    return [r for r in rows if r.reversal_of_id is None and r.id not in reversed_ids]


async def load_landed_cost_flow(db: AsyncSession, *, company_id: int, product_ids: set[int]):
    """Caller serializes the company and calls this after physical orchestration.

    Returns graph and a mapping from receipt document-line ID to root node.
    Only products with active landed costs participate. Unrelated physical
    histories cannot prevent a valid adjustment for these products.
    """
    async def rows(model):
        statement = select(model).where(model.company_id == company_id)
        if hasattr(model, "product_id"):
            statement = statement.where(model.product_id.in_(product_ids))
        if model is InventoryCostEntry:
            statement = statement.join(DocumentLine, DocumentLine.id == model.document_line_id).where(
                DocumentLine.product_id.in_(product_ids))
        return list((await db.execute(statement.order_by(model.id).execution_options(populate_existing=True))).scalars().all())

    # Return lifecycle owns physical activation through immutable events; its
    # technical warehouse document deliberately stays draft.
    active_returns = _active(await rows(TradeReturnEvent))
    active_return_documents = {r.return_document_id for r in active_returns}
    documents = {d.id: d for d in await rows(Document)
                 if d.status == "posted" or d.id in active_return_documents}
    document_lines = {r.id: r for r in (await db.execute(
        select(DocumentLine).join(Document, Document.id == DocumentLine.document_id)
        .where(Document.company_id == company_id)
    )).scalars().all()}
    costs = {c.document_line_id: c for c in await rows(InventoryCostEntry)
             if c.document_id in documents}
    lots = {r.id: r for r in await rows(StockLot) if r.source_document_id in documents}
    lot_by_line = {r.source_document_line_id: r for r in lots.values()}
    consumptions = [c for c in await rows(StockLotConsumption) if c.issue_document_id in documents]
    by_lot = defaultdict(list)
    for consumption in consumptions:
        by_lot[consumption.stock_lot_id].append(consumption)
    layers = [r for r in await rows(WarehouseTransferValuationLayer)
              if r.destination_receipt_document_id in documents]
    fifo_transfer = {r.source_stock_lot_consumption_id: r for r in layers
                     if r.source_stock_lot_consumption_id is not None}
    ma_transfer = defaultdict(list)
    for layer in layers:
        if layer.source_stock_lot_consumption_id is None:
            ma_transfer[layer.source_inventory_cost_entry_id].append(layer)
    purchase_return_quantities = defaultdict(lambda: ZERO)
    for returned in active_returns:
        if returned.direction == "purchase":
            purchase_return_quantities[returned.return_document_line_id] += returned.returned_quantity
    returns = {r.id: r for r in active_returns if r.direction == "sale"}
    restorations = {r.id: r for r in _active(await rows(SalesReturnCostRestorationEvent))
                    if r.trade_return_event_id in returns}
    fifo_returns = defaultdict(list)
    for part in await rows(SalesReturnCostRestorationFifoSlice):
        parent = restorations.get(part.sales_return_cost_restoration_event_id)
        if parent:
            returned = returns[parent.trade_return_event_id]
            fifo_returns[part.fifo_consumption_id].append((returned, part.restored_quantity))
    ma_returns = defaultdict(list)
    for restoration in restorations.values():
        if restoration.valuation_method == "weighted_average_moving":
            ma_returns[restoration.inventory_cost_entry_id].append(
                (returns[restoration.trade_return_event_id], restoration.restored_quantity)
            )

    graph = {}
    roots = {}

    def issued(cost):
        line = document_lines[cost.document_line_id]
        returned_quantity = purchase_return_quantities.get(line.id, ZERO)
        if returned_quantity and returned_quantity != cost.quantity:
            raise PurchaseLandedCostFlowError("Purchase-return quantity must match its physical issue")
        return CostDestination(
            f"issue:{cost.id}", "expensed" if returned_quantity else "issued", line.product_id, line.warehouse_id,
            documents[cost.document_id].document_date, inventory_cost_entry_id=cost.id,
        )

    with localcontext() as ctx:
        ctx.prec = 50
        for lot in lots.values():
            key = f"fifo:{lot.id}"
            roots[lot.source_document_line_id] = key
            if not lot.original_quantity.is_finite() or lot.original_quantity <= 0:
                continue  # A reachable invalid node will fail in the calculator.
            edges = [CostFlowEdge(
                lot.remaining_quantity / lot.original_quantity,
                destination=CostDestination(key, "on_hand", lot.product_id, lot.warehouse_id,
                                            date.min, stock_lot_id=lot.id),
            )]
            for consumption in by_lot[lot.id]:
                fraction = consumption.quantity / lot.original_quantity
                transfer = fifo_transfer.get(consumption.id)
                if transfer:
                    destination_lot = lot_by_line.get(transfer.destination_receipt_document_line_id)
                    if destination_lot is None or transfer.quantity != consumption.quantity:
                        raise PurchaseLandedCostFlowError("Invalid FIFO transfer provenance")
                    edges.append(CostFlowEdge(fraction, target_node=f"fifo:{destination_lot.id}"))
                    continue
                returned_quantity = ZERO
                for returned, quantity in fifo_returns[consumption.id]:
                    destination_lot = lot_by_line.get(returned.return_document_line_id)
                    if destination_lot is None:
                        raise PurchaseLandedCostFlowError("Missing FIFO return lot")
                    returned_quantity += quantity
                    edges.append(CostFlowEdge(quantity / lot.original_quantity,
                                              target_node=f"fifo:{destination_lot.id}"))
                remaining = consumption.quantity - returned_quantity
                cost = costs.get(consumption.issue_document_line_id)
                if remaining < 0 or cost is None:
                    raise PurchaseLandedCostFlowError("Invalid FIFO issued/returned quantity")
                if remaining:
                    edges.append(CostFlowEdge(remaining / lot.original_quantity, destination=issued(cost)))
            graph[key] = tuple(edges)

        movements = [m for m in _active(await rows(MovingAverageMovement))
                     if m.document_id in documents]
        streams = defaultdict(list)
        receipt_by_line = {}
        for movement in movements:
            streams[(movement.product_id, movement.warehouse_id)].append(movement)
            if movement.quantity_delta > 0:
                receipt_by_line[movement.document_line_id] = movement
                roots[movement.document_line_id] = f"ma:{movement.id}"
        for (product_id, warehouse_id), stream in streams.items():
            before = {}
            quantity = ZERO
            for movement in stream:
                before[movement.id] = quantity
                quantity += movement.quantity_delta
                if quantity < 0:
                    raise PurchaseLandedCostFlowError("Active moving-average quantity becomes negative")
            for start, receipt in enumerate(stream):
                if receipt.quantity_delta <= 0:
                    continue
                key = f"ma:{receipt.id}"
                edges = []
                remaining_weight = ONE
                for movement in stream[start + 1:]:
                    if movement.quantity_delta >= 0:
                        continue
                    issued_quantity = -movement.quantity_delta
                    if before[movement.id] <= 0 or issued_quantity > before[movement.id]:
                        raise PurchaseLandedCostFlowError("Invalid moving-average issue quantity")
                    fraction = remaining_weight * issued_quantity / before[movement.id]
                    remaining_weight -= fraction
                    if not fraction:
                        continue
                    cost = costs.get(movement.document_line_id)
                    if cost is None:
                        raise PurchaseLandedCostFlowError("Missing issued moving-average cost entry")
                    routed_quantity = ZERO
                    for transfer in ma_transfer[cost.id]:
                        target = receipt_by_line.get(transfer.destination_receipt_document_line_id)
                        if target is None:
                            raise PurchaseLandedCostFlowError("Missing moving-average transfer receipt")
                        routed_quantity += transfer.quantity
                        edges.append(CostFlowEdge(fraction * transfer.quantity / issued_quantity,
                                                  target_node=f"ma:{target.id}"))
                    for returned, returned_quantity in ma_returns[cost.id]:
                        target = receipt_by_line.get(returned.return_document_line_id)
                        if target is None:
                            raise PurchaseLandedCostFlowError("Missing moving-average return receipt")
                        routed_quantity += returned_quantity
                        edges.append(CostFlowEdge(fraction * returned_quantity / issued_quantity,
                                                  target_node=f"ma:{target.id}"))
                    if routed_quantity > issued_quantity:
                        raise PurchaseLandedCostFlowError("Routed quantity exceeds original issue")
                    if routed_quantity < issued_quantity:
                        edges.append(CostFlowEdge(fraction * (issued_quantity - routed_quantity) / issued_quantity,
                                                  destination=issued(cost)))
                if remaining_weight:
                    edges.append(CostFlowEdge(remaining_weight, destination=CostDestination(
                        f"ma-stock:{product_id}:{warehouse_id}", "on_hand", product_id, warehouse_id, date.min,
                    )))
                graph[key] = tuple(edges)
    return graph, roots
