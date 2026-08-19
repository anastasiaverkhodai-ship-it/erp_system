from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.document import (
    Document,
    DocumentStatus,
    DocumentType,
)
from app.models.product import Product
from app.models.stock_balance import StockBalance
from app.models.stock_ledger import (
    StockLedger,
    StockMovementType,
)
from app.models.warehouse import Warehouse
from app.services.accounting_period_service import ensure_period_open
from app.services.inventory_costing import (
    InventoryCostingError,
    process_inventory_issue,
    process_inventory_receipt,
)


class DocumentPostingError(Exception):
    """Business error raised when a document cannot be posted."""


class DocumentNotFoundError(DocumentPostingError):
    """Raised when the requested document does not exist."""


async def get_stock_balance(
    db: AsyncSession,
    company_id: int,
    product_id: int,
    warehouse_id: int,
) -> Decimal:
    result = await db.execute(
        select(StockBalance.quantity).where(
            StockBalance.company_id == company_id,
            StockBalance.product_id == product_id,
            StockBalance.warehouse_id == warehouse_id,
        )
    )

    quantity = result.scalar_one_or_none()

    if quantity is None:
        return Decimal("0")

    return Decimal(quantity)


async def get_locked_stock_balance(
    db: AsyncSession,
    company_id: int,
    product_id: int,
    warehouse_id: int,
) -> StockBalance:
    now = datetime.now(
        timezone.utc
    ).replace(tzinfo=None)

    # Ensure that a balance row exists.
    # If another transaction already created it,
    # PostgreSQL simply ignores this INSERT.
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

    # Lock this exact balance row until COMMIT / ROLLBACK.
    result = await db.execute(
        select(StockBalance)
        .where(
            StockBalance.company_id == company_id,
            StockBalance.product_id == product_id,
            StockBalance.warehouse_id == warehouse_id,
        )
        .with_for_update()
    )

    balance = result.scalar_one()

    return balance


async def post_document(
    db: AsyncSession,
    company_id: int,
    document_id: int,
) -> Document:
    result = await db.execute(
        select(Document)
        .options(
            selectinload(Document.lines)
        )
        .where(
            Document.id == document_id,
            Document.company_id == company_id,
        )
        .with_for_update()
    )

    document = result.scalar_one_or_none()

    if document is None:
        raise DocumentNotFoundError(
            "Document not found"
        )

    if document.status != DocumentStatus.DRAFT:
        raise DocumentPostingError(
            "Only draft documents can be posted"
        )

    if not document.lines:
        raise DocumentPostingError(
            "Document has no lines"
        )

    await ensure_period_open(
        company_id=document.company_id,
        operation_date=document.document_date,
        db=db,
    )

    stock_deltas: dict[
        tuple[int, int],
        Decimal,
    ] = {}

    # ---------------------------------------------------------
    # VALIDATION AND DELTA CALCULATION
    # ---------------------------------------------------------

    for line in document.lines:
        product_result = await db.execute(
            select(Product).where(
                Product.id == line.product_id,
                Product.company_id == document.company_id,
                Product.is_active.is_(True),
            )
        )

        product = product_result.scalar_one_or_none()

        if product is None:
            raise DocumentPostingError(
                f"Product {line.product_id} "
                f"is invalid or does not belong "
                f"to this company"
            )

        warehouse_result = await db.execute(
            select(Warehouse).where(
                Warehouse.id == line.warehouse_id,
                Warehouse.company_id == document.company_id,
                Warehouse.is_active.is_(True),
            )
        )

        warehouse = warehouse_result.scalar_one_or_none()

        if warehouse is None:
            raise DocumentPostingError(
                f"Warehouse {line.warehouse_id} "
                f"is invalid or does not belong "
                f"to this company"
            )

        if line.price < Decimal("0"):
            raise DocumentPostingError(
                f"Invalid price in line {line.id}"
            )

        if document.document_type in (
            DocumentType.RECEIPT,
            DocumentType.ISSUE,
        ):
            if line.quantity <= Decimal("0"):
                raise DocumentPostingError(
                    f"Quantity must be greater than zero "
                    f"in line {line.id}"
                )

        elif (
            document.document_type
            == DocumentType.ADJUSTMENT
        ):
            if line.quantity == Decimal("0"):
                raise DocumentPostingError(
                    f"Adjustment quantity cannot be zero "
                    f"in line {line.id}"
                )

        else:
            raise DocumentPostingError(
                f"Unsupported document type: "
                f"{document.document_type}"
            )

        key = (
            line.product_id,
            line.warehouse_id,
        )

        if (
            document.document_type
            == DocumentType.RECEIPT
        ):
            delta = line.quantity

        elif (
            document.document_type
            == DocumentType.ISSUE
        ):
            delta = -line.quantity

        elif (
            document.document_type
            == DocumentType.ADJUSTMENT
        ):
            delta = line.quantity

        else:
            raise DocumentPostingError(
                f"Unsupported document type: "
                f"{document.document_type}"
            )

        stock_deltas[key] = (
            stock_deltas.get(
                key,
                Decimal("0"),
            )
            + delta
        )

    # ---------------------------------------------------------
    # LOCK AND UPDATE STOCK BALANCES
    # ---------------------------------------------------------

    # Always lock balances in the same order.
    # This reduces the risk of deadlocks when documents
    # contain multiple products / warehouses.
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
            company_id=document.company_id,
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
            raise DocumentPostingError(
                f"Insufficient stock for product "
                f"{product_id}. "
                f"Available: {current_quantity}, "
                f"required change: {delta}"
            )

        balance.quantity = new_quantity

        balance.updated_at = datetime.now(
            timezone.utc
        ).replace(tzinfo=None)

    # ---------------------------------------------------------
    # CREATE STOCK LEDGER MOVEMENTS
    # ---------------------------------------------------------

    for line in document.lines:
        if (
            document.document_type
            == DocumentType.RECEIPT
        ):
            movement_type = (
                StockMovementType.RECEIPT
            )

            movement_quantity = (
                line.quantity
            )

        elif (
            document.document_type
            == DocumentType.ISSUE
        ):
            movement_type = (
                StockMovementType.ISSUE
            )

            movement_quantity = (
                -line.quantity
            )

        elif (
            document.document_type
            == DocumentType.ADJUSTMENT
        ):
            movement_type = (
                StockMovementType.ADJUSTMENT
            )

            movement_quantity = (
                line.quantity
            )

        else:
            raise DocumentPostingError(
                f"Unsupported document type: "
                f"{document.document_type}"
            )

        db.add(
            StockLedger(
                company_id=document.company_id,
                document_id=document.id,
                document_line_id=line.id,
                product_id=line.product_id,
                warehouse_id=line.warehouse_id,
                quantity=movement_quantity,
                movement_type=movement_type,
                movement_date=document.document_date,
            )
        )

        if (
            document.document_type
            == DocumentType.RECEIPT
        ):
            try:
                await process_inventory_receipt(
                    db=db,
                    document=document,
                    line=line,
                )
            except InventoryCostingError as exc:
                raise DocumentPostingError(
                    str(exc)
                ) from exc

        elif (
            document.document_type
            == DocumentType.ISSUE
        ):
            try:
                await process_inventory_issue(
                    db=db,
                    document=document,
                    line=line,
                )
            except InventoryCostingError as exc:
                raise DocumentPostingError(
                    str(exc)
                ) from exc
    # ---------------------------------------------------------
    # MARK DOCUMENT AS POSTED
    # ---------------------------------------------------------

    document.status = DocumentStatus.POSTED

    document.posted_at = datetime.now(
        timezone.utc
    ).replace(tzinfo=None)

    await db.flush()

    return document