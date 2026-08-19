from decimal import Decimal

from app.models.document import DocumentType
from app.models.stock_ledger import (
    StockLedger,
    StockMovementType,
)
from app.services.inventory_costing import (
    InventoryCostingError,
    process_inventory_issue,
    process_inventory_receipt,
)
from app.services.posting_context import PostingContext
from app.services.warehouse_posting import (
    WarehousePostingError,
    calculate_stock_deltas,
    get_locked_stock_balance,
)

from app.services.posting_handler import (
    PostingHandlerError,
)


class WarehousePostingHandlerError(
    PostingHandlerError
):
    """Business error raised by the warehouse posting handler."""


class WarehousePostingHandler:
    async def post(
        self,
        context: PostingContext,
    ) -> None:
        document = context.document
        db = context.db

        # -----------------------------------------------------
        # VALIDATION AND STOCK DELTA CALCULATION
        # -----------------------------------------------------

        try:
            stock_deltas = await calculate_stock_deltas(
                context
            )
        except WarehousePostingError as exc:
            raise WarehousePostingHandlerError(
                str(exc)
            ) from exc

        # -----------------------------------------------------
        # LOCK AND UPDATE STOCK BALANCES
        # -----------------------------------------------------

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
                raise WarehousePostingHandlerError(
                    f"Insufficient stock for product "
                    f"{product_id}. "
                    f"Available: {current_quantity}, "
                    f"required change: {delta}"
                )

            balance.quantity = new_quantity
            balance.updated_at = context.posting_time

        # -----------------------------------------------------
        # STOCK LEDGER AND INVENTORY COSTING
        # -----------------------------------------------------

        for line in document.lines:
            if (
                context.document_type
                == DocumentType.RECEIPT
            ):
                movement_type = (
                    StockMovementType.RECEIPT
                )
                movement_quantity = line.quantity

            elif (
                context.document_type
                == DocumentType.ISSUE
            ):
                movement_type = (
                    StockMovementType.ISSUE
                )
                movement_quantity = -line.quantity

            elif (
                context.document_type
                == DocumentType.ADJUSTMENT
            ):
                movement_type = (
                    StockMovementType.ADJUSTMENT
                )
                movement_quantity = line.quantity

            else:
                raise WarehousePostingHandlerError(
                    f"Unsupported document type: "
                    f"{context.document_type}"
                )

            db.add(
                StockLedger(
                    company_id=context.company_id,
                    document_id=context.document_id,
                    document_line_id=line.id,
                    product_id=line.product_id,
                    warehouse_id=line.warehouse_id,
                    quantity=movement_quantity,
                    movement_type=movement_type,
                    movement_date=context.operation_date,
                )
            )

            # -------------------------------------------------
            # INVENTORY COSTING
            # -------------------------------------------------

            if (
                context.document_type
                == DocumentType.RECEIPT
            ):
                try:
                    await process_inventory_receipt(
                        db=db,
                        document=document,
                        line=line,
                    )
                except InventoryCostingError as exc:
                    raise WarehousePostingHandlerError(
                        str(exc)
                    ) from exc

            elif (
                context.document_type
                == DocumentType.ISSUE
            ):
                try:
                    await process_inventory_issue(
                        db=db,
                        document=document,
                        line=line,
                    )
                except InventoryCostingError as exc:
                    raise WarehousePostingHandlerError(
                        str(exc)
                    ) from exc