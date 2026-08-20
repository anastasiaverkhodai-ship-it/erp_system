from decimal import Decimal

from app.models.stock_ledger import (
    StockLedger,
    StockMovementType,
)
from app.services.inventory_costing import (
    InventoryCostingError,
    reverse_inventory_costing,
)
from app.services.reversal_context import ReversalContext
from app.services.reversal_handler import ReversalHandlerError
from app.services.warehouse_posting import (
    get_locked_stock_balance,
)


class WarehouseReversalHandlerError(
    ReversalHandlerError
):
    """Business error raised by the warehouse reversal handler."""


class WarehouseReversalHandler:
    async def reverse(
        self,
        context: ReversalContext,
    ) -> None:
        db = context.db
        document = context.document
        original_movements = (
            context.original_stock_movements
        )

        if not original_movements:
            raise WarehouseReversalHandlerError(
                "Document has no stock movements to reverse"
            )

        # -----------------------------------------------------
        # CALCULATE REVERSAL DELTAS
        # -----------------------------------------------------

        stock_deltas: dict[
            tuple[int, int],
            Decimal,
        ] = {}

        for movement in original_movements:
            key = (
                movement.product_id,
                movement.warehouse_id,
            )

            reversal_quantity = (
                -Decimal(movement.quantity)
            )

            stock_deltas[key] = (
                stock_deltas.get(
                    key,
                    Decimal("0"),
                )
                + reversal_quantity
            )

        # -----------------------------------------------------
        # LOCK AND UPDATE STOCK BALANCES
        # -----------------------------------------------------

        # Keep the same lock order as posting:
        # StockBalance first, inventory costing second.
        for (
            product_id,
            warehouse_id,
        ) in sorted(stock_deltas):
            delta = stock_deltas[
                (
                    product_id,
                    warehouse_id,
                )
            ]

            balance = await get_locked_stock_balance(
                db=db,
                company_id=context.company_id,
                product_id=product_id,
                warehouse_id=warehouse_id,
            )

            current_quantity = Decimal(
                balance.quantity
            )

            new_quantity = (
                current_quantity
                + delta
            )

            if new_quantity < Decimal("0"):
                raise WarehouseReversalHandlerError(
                    f"Cannot reverse document because "
                    f"stock would become negative for "
                    f"product {product_id}. "
                    f"Available: {current_quantity}, "
                    f"reversal change: {delta}"
                )

            balance.quantity = new_quantity
            balance.updated_at = context.reversal_time

        # -----------------------------------------------------
        # REVERSE INVENTORY COSTING
        # -----------------------------------------------------

        try:
            await reverse_inventory_costing(
                db=db,
                document=document,
                reversal_date=context.reversal_date,
            )
        except InventoryCostingError as exc:
            raise WarehouseReversalHandlerError(
                str(exc)
            ) from exc

        # -----------------------------------------------------
        # CREATE REVERSAL STOCK MOVEMENTS
        # -----------------------------------------------------

        for movement in original_movements:
            db.add(
                StockLedger(
                    company_id=movement.company_id,
                    document_id=context.document_id,
                    document_line_id=(
                        movement.document_line_id
                    ),
                    product_id=movement.product_id,
                    warehouse_id=movement.warehouse_id,
                    quantity=(
                        -Decimal(movement.quantity)
                    ),
                    movement_type=(
                        StockMovementType.REVERSAL
                    ),
                    movement_date=context.reversal_date,
                )
            )