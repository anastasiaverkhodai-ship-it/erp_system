from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.company import InventoryValuationMethod
from app.services.warehouse_transfer_history_service import (
    WarehouseTransferHistoryAction,
    WarehouseTransferHistoryPlan,
    WarehouseTransferTarget,
)
from app.services.warehouse_transfer_persistence_service import (
    append_warehouse_transfer_event,
    append_warehouse_transfer_line,
    append_warehouse_transfer_valuation_layer,
)
from app.services.warehouse_transfer_physical_factory import (
    WarehouseTransferPhysicalFactory,
    WarehouseTransferPhysicalLine,
)
from app.services.warehouse_transfer_reconciliation_service import (
    prepare_warehouse_transfer_reconciliation,
)


class WarehouseTransferExecutionError(ValueError):
    pass


def _decimal(
    value,
    *,
    field: str,
) -> Decimal:
    try:
        result = Decimal(value)
    except Exception as exc:
        raise WarehouseTransferExecutionError(
            f"{field} must be a valid decimal"
        ) from exc

    if not result.is_finite():
        raise WarehouseTransferExecutionError(
            f"{field} must be finite"
        )

    return result


def _validate_physical_line(
    *,
    physical_line: WarehouseTransferPhysicalLine,
    target_quantity: Decimal,
) -> None:
    """
    Validate physical result BEFORE immutable provenance rows
    are appended.

    FIFO:
      N layers allowed.
      Every layer must identify one source FIFO consumption.

    Moving average:
      exactly one layer.
      FIFO consumption provenance must be NULL.
    """

    physical_quantity = _decimal(
        physical_line.quantity,
        field="physical line quantity",
    )

    if physical_quantity != target_quantity:
        raise WarehouseTransferExecutionError(
            "physical transfer result does not match target"
        )

    layers = tuple(
        physical_line.valuation_layers
    )

    if not layers:
        raise WarehouseTransferExecutionError(
            "transfer line requires valuation provenance"
        )

    layer_quantity = Decimal("0")
    layer_value = Decimal("0")

    methods = set()
    destination_line_ids = set()
    source_fifo_ids = set()

    for layer in layers:
        quantity = _decimal(
            layer.quantity,
            field="valuation layer quantity",
        )
        unit_cost = _decimal(
            layer.unit_cost,
            field="valuation layer unit_cost",
        )
        valuation_amount = _decimal(
            layer.valuation_amount,
            field="valuation layer valuation_amount",
        )

        if quantity <= 0:
            raise WarehouseTransferExecutionError(
                "valuation layer quantity must be positive"
            )

        if unit_cost < 0:
            raise WarehouseTransferExecutionError(
                "valuation layer unit_cost cannot be negative"
            )

        if valuation_amount < 0:
            raise WarehouseTransferExecutionError(
                "valuation layer valuation_amount cannot be negative"
            )


        if (
            layer.destination_receipt_document_id
            != physical_line.receipt_document_id
        ):
            raise WarehouseTransferExecutionError(
                "valuation layer receipt document does not "
                "match business line"
            )

        destination_line_id = (
            layer.destination_receipt_document_line_id
        )

        if destination_line_id in destination_line_ids:
            raise WarehouseTransferExecutionError(
                "duplicate destination receipt line in "
                "valuation layers"
            )

        destination_line_ids.add(
            destination_line_id
        )

        methods.add(
            layer.valuation_method
        )

        if (
            layer.source_stock_lot_consumption_id
            is not None
        ):
            if (
                layer.source_stock_lot_consumption_id
                in source_fifo_ids
            ):
                raise WarehouseTransferExecutionError(
                    "duplicate source FIFO consumption in "
                    "valuation layers"
                )

            source_fifo_ids.add(
                layer.source_stock_lot_consumption_id
            )

        layer_quantity += quantity
        layer_value += valuation_amount

    if layer_quantity != target_quantity:
        raise WarehouseTransferExecutionError(
            "valuation layer quantities do not sum "
            "to transfer line quantity"
        )

    if len(methods) != 1:
        raise WarehouseTransferExecutionError(
            "one transfer business line cannot mix "
            "valuation methods"
        )

    method = next(
        iter(methods)
    )

    if method == InventoryValuationMethod.FIFO:
        for layer in layers:
            if (
                _decimal(
                    layer.valuation_amount,
                    field="FIFO layer valuation_amount",
                )
                !=
                _decimal(
                    layer.quantity,
                    field="FIFO layer quantity",
                )
                *
                _decimal(
                    layer.unit_cost,
                    field="FIFO layer unit_cost",
                )
            ):
                raise WarehouseTransferExecutionError(
                    "FIFO valuation layer amount must equal "
                    "quantity multiplied by unit_cost"
                )

        if any(
            layer.source_stock_lot_consumption_id
            is None
            for layer in layers
        ):
            raise WarehouseTransferExecutionError(
                "FIFO valuation layer requires source "
                "StockLotConsumption provenance"
            )

    elif (
        method
        == InventoryValuationMethod.WEIGHTED_AVERAGE_MOVING
    ):
        if len(layers) != 1:
            raise WarehouseTransferExecutionError(
                "moving-average transfer requires exactly "
                "one valuation layer"
            )

        if (
            layers[0].source_stock_lot_consumption_id
            is not None
        ):
            raise WarehouseTransferExecutionError(
                "moving-average valuation layer must not "
                "reference FIFO consumption"
            )

    else:
        raise WarehouseTransferExecutionError(
            "unsupported transfer valuation method"
        )

    # Source ISSUE creates one aggregate ICE. Every layer of the
    # same business line must therefore point to the same ICE.
    source_ice_ids = {
        layer.source_inventory_cost_entry_id
        for layer in layers
    }

    if len(source_ice_ids) != 1:
        raise WarehouseTransferExecutionError(
            "all valuation layers of one transfer line must "
            "reference the same source InventoryCostEntry"
        )

    if layer_value < 0:
        raise WarehouseTransferExecutionError(
            "transfer valuation cannot be negative"
        )


async def execute_warehouse_transfer_reconciliation(
    db: AsyncSession,
    *,
    factory: WarehouseTransferPhysicalFactory | None = None,
    company_id: int,
    history_key: str,
    target: WarehouseTransferTarget | None,
    adjustment_date: date | None,
    created_by: int,
) -> WarehouseTransferHistoryPlan:
    """
    Execute one immutable transfer correction chain.

    Caller owns COMMIT / ROLLBACK.

    CREATE:
        physical source ISSUE + destination RECEIPT
        -> validate full valuation provenance
        -> append immutable transfer event
        -> append business transfer lines
        -> append valuation layers

    REVERSE:
        reverse exact physical provenance
        -> append immutable reversal metadata

    REPLACE:
        reverse old
        -> append reversal
        -> create replacement physical operation
        -> append replacement immutable provenance
    """
    if factory is None:
        from app.services.warehouse_transfer_physical_factory import (
            DefaultWarehouseTransferPhysicalFactory,
        )

        factory = (
            DefaultWarehouseTransferPhysicalFactory()
        )


    plan = await prepare_warehouse_transfer_reconciliation(
        db,
        company_id=company_id,
        history_key=history_key,
        target=target,
        adjustment_date=adjustment_date,
    )

    if plan.action == WarehouseTransferHistoryAction.NOOP:
        return plan

    original = plan.active_original

    if plan.action in {
        WarehouseTransferHistoryAction.REVERSE,
        WarehouseTransferHistoryAction.REVERSE_AND_REPLACE,
    }:
        if original is None:
            raise WarehouseTransferExecutionError(
                "reversal requires active original"
            )

        if original.target is None:
            raise WarehouseTransferExecutionError(
                "active original requires target"
            )

        if plan.reversal_date is None:
            raise WarehouseTransferExecutionError(
                "reversal_date is required"
            )

        # Note:
        # current factory interface still receives this history item.
        # Real physical factory implementation will load exact persisted
        # event/provenance by original.id before reversing it.
        await factory.reverse_transfer(
            db,
            original_event=original,
            reversal_date=plan.reversal_date,
            created_by=created_by,
        )

        await append_warehouse_transfer_event(
            db,
            company_id=original.target.company_id,
            history_key=plan.history_key,
            source_warehouse_id=(
                original.target.source_warehouse_id
            ),
            destination_warehouse_id=(
                original.target.destination_warehouse_id
            ),
            transfer_date=plan.reversal_date,
            created_by=created_by,
            reversal_of_id=original.id,
        )

    if plan.action not in {
        WarehouseTransferHistoryAction.CREATE,
        WarehouseTransferHistoryAction.REVERSE_AND_REPLACE,
    }:
        return plan

    if plan.target is None:
        raise WarehouseTransferExecutionError(
            "create/replacement requires target"
        )

    physical = await factory.create_transfer(
        db,
        target=plan.target,
        created_by=created_by,
    )

    target_by_product = {
        line.product_id: Decimal(
            line.quantity
        )
        for line in plan.target.lines
    }

    physical_by_product = {}

    for physical_line in physical.lines:
        if physical_line.product_id in physical_by_product:
            raise WarehouseTransferExecutionError(
                "duplicate product in physical transfer result"
            )

        physical_by_product[
            physical_line.product_id
        ] = physical_line

    if set(physical_by_product) != set(target_by_product):
        raise WarehouseTransferExecutionError(
            "physical transfer result does not match target"
        )

    # Validate the complete physical result before persisting
    # transfer-domain provenance.
    for product_id, target_quantity in target_by_product.items():
        _validate_physical_line(
            physical_line=physical_by_product[
                product_id
            ],
            target_quantity=target_quantity,
        )

    event = await append_warehouse_transfer_event(
        db,
        company_id=plan.target.company_id,
        history_key=plan.history_key,
        source_warehouse_id=(
            plan.target.source_warehouse_id
        ),
        destination_warehouse_id=(
            plan.target.destination_warehouse_id
        ),
        transfer_date=plan.target.transfer_date,
        created_by=created_by,
    )

    for product_id in sorted(
        physical_by_product
    ):
        physical_line = physical_by_product[
            product_id
        ]

        transfer_line = await append_warehouse_transfer_line(
            db,
            company_id=plan.target.company_id,
            transfer_event_id=event.id,
            product_id=physical_line.product_id,
            source_warehouse_id=(
                plan.target.source_warehouse_id
            ),
            destination_warehouse_id=(
                plan.target.destination_warehouse_id
            ),
            quantity=physical_line.quantity,
            issue_document_id=(
                physical_line.issue_document_id
            ),
            issue_document_line_id=(
                physical_line.issue_document_line_id
            ),
            receipt_document_id=(
                physical_line.receipt_document_id
            ),
        )

        for layer in physical_line.valuation_layers:
            await append_warehouse_transfer_valuation_layer(
                db,
                company_id=plan.target.company_id,
                transfer_line_id=transfer_line.id,
                product_id=physical_line.product_id,
                destination_warehouse_id=(
                    plan.target.destination_warehouse_id
                ),
                valuation_method=(
                    layer.valuation_method
                ),
                quantity=layer.quantity,
                unit_cost=layer.unit_cost,
                valuation_amount=(
                    layer.valuation_amount
                ),
                source_inventory_cost_entry_id=(
                    layer.source_inventory_cost_entry_id
                ),
                source_stock_lot_consumption_id=(
                    layer.source_stock_lot_consumption_id
                ),
                destination_receipt_document_id=(
                    layer.destination_receipt_document_id
                ),
                destination_receipt_document_line_id=(
                    layer.destination_receipt_document_line_id
                ),
            )

    return plan
