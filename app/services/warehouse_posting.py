from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import DocumentType
from app.models.product import Product
from app.models.stock_balance import StockBalance
from app.models.warehouse import Warehouse
from app.services.posting_context import PostingContext
from app.services.posting_types import StockDeltas

class WarehousePostingError(Exception):
    """Business error raised during warehouse posting validation."""
async def get_locked_stock_balance(
    db: AsyncSession,
    company_id: int,
    product_id: int,
    warehouse_id: int,
) -> StockBalance:
    now = datetime.now(
        timezone.utc
    ).replace(tzinfo=None)

    statement = (
        pg_insert(StockBalance)
        .values(
            company_id=company_id,
            product_id=product_id,
            warehouse_id=warehouse_id,
            quantity=Decimal("0"),
            updated_at=now,
        )
        .on_conflict_do_nothing(
            constraint="uq_stock_balance_company_product_warehouse"
        )
    )

    await db.execute(statement)

    result = await db.execute(
        select(StockBalance)
        .where(
            StockBalance.company_id == company_id,
            StockBalance.product_id == product_id,
            StockBalance.warehouse_id == warehouse_id,
        )
        .with_for_update()
    )

    return result.scalar_one()

async def calculate_stock_deltas(
    context: PostingContext,
) -> StockDeltas:
    document = context.document
    db = context.db

    stock_deltas: StockDeltas = {}

    for line in document.lines:
        product_result = await db.execute(
            select(Product).where(
                Product.id == line.product_id,
                Product.company_id == context.company_id,
                Product.is_active.is_(True),
            )
        )

        product = product_result.scalar_one_or_none()

        if product is None:
            raise WarehousePostingError(
                f"Product {line.product_id} "
                f"is invalid or does not belong "
                f"to this company"
            )

        warehouse_result = await db.execute(
            select(Warehouse).where(
                Warehouse.id == line.warehouse_id,
                Warehouse.company_id == context.company_id,
                Warehouse.is_active.is_(True),
            )
        )

        warehouse = warehouse_result.scalar_one_or_none()

        if warehouse is None:
            raise WarehousePostingError(
                f"Warehouse {line.warehouse_id} "
                f"is invalid or does not belong "
                f"to this company"
            )

        if line.price < Decimal("0"):
            raise WarehousePostingError(
                f"Invalid price in line {line.id}"
            )

        if context.document_type in (
            DocumentType.RECEIPT,
            DocumentType.ISSUE,
        ):
            if line.quantity <= Decimal("0"):
                raise WarehousePostingError(
                    f"Quantity must be greater than zero "
                    f"in line {line.id}"
                )

        elif (
            context.document_type
            == DocumentType.ADJUSTMENT
        ):
            if line.quantity == Decimal("0"):
                raise WarehousePostingError(
                    f"Adjustment quantity cannot be zero "
                    f"in line {line.id}"
                )

        else:
            raise WarehousePostingError(
                f"Unsupported document type: "
                f"{context.document_type}"
            )

        key = (
            line.product_id,
            line.warehouse_id,
        )

        if (
            context.document_type
            == DocumentType.RECEIPT
        ):
            delta = line.quantity

        elif (
            context.document_type
            == DocumentType.ISSUE
        ):
            delta = -line.quantity

        elif (
            context.document_type
            == DocumentType.ADJUSTMENT
        ):
            delta = line.quantity

        else:
            raise WarehousePostingError(
                f"Unsupported document type: "
                f"{context.document_type}"
            )

        stock_deltas[key] = (
            stock_deltas.get(
                key,
                Decimal("0"),
            )
            + delta
        )

    return stock_deltas