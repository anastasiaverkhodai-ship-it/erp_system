from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import (
    Document,
    DocumentStatus,
)
from app.models.stock_ledger import (
    StockLedger,
    StockMovementType,
)
from app.services.accounting_period_service import ensure_period_open
from app.services.document_posting import get_locked_stock_balance


class DocumentReversalError(Exception):
    """Business error raised when a document cannot be reversed."""


class DocumentReversalNotFoundError(DocumentReversalError):
    """Raised when the requested document does not exist."""


async def reverse_document(
    db: AsyncSession,
    company_id: int,
    document_id: int,
    reversal_date: date,
    reversed_by: int,
) -> Document:
    # ---------------------------------------------------------
    # LOCK DOCUMENT
    # ---------------------------------------------------------

    result = await db.execute(
        select(Document)
        .where(
            Document.id == document_id,
            Document.company_id == company_id,
        )
        .with_for_update()
    )

    document = result.scalar_one_or_none()

    if document is None:
        raise DocumentReversalNotFoundError(
            "Document not found"
        )

    if document.status != DocumentStatus.POSTED:
        raise DocumentReversalError(
            "Only posted documents can be reversed"
        )

    # ---------------------------------------------------------
    # CHECK REVERSAL ACCOUNTING PERIOD
    # ---------------------------------------------------------

    await ensure_period_open(
        company_id=document.company_id,
        operation_date=reversal_date,
        db=db,
    )

    # ---------------------------------------------------------
    # LOAD ORIGINAL STOCK MOVEMENTS
    # ---------------------------------------------------------

    movements_result = await db.execute(
        select(StockLedger)
        .where(
            StockLedger.company_id == company_id,
            StockLedger.document_id == document.id,
            StockLedger.movement_type
            != StockMovementType.REVERSAL,
        )
        .order_by(StockLedger.id)
    )

    original_movements = (
        movements_result.scalars().all()
    )

    if not original_movements:
        raise DocumentReversalError(
            "Document has no stock movements to reverse"
        )

    # ---------------------------------------------------------
    # CALCULATE REVERSAL DELTAS
    # ---------------------------------------------------------

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

    # ---------------------------------------------------------
    # LOCK AND UPDATE STOCK BALANCES
    # ---------------------------------------------------------

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
            company_id=company_id,
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
            raise DocumentReversalError(
                f"Cannot reverse document because "
                f"stock would become negative for "
                f"product {product_id}. "
                f"Available: {current_quantity}, "
                f"reversal change: {delta}"
            )

        balance.quantity = new_quantity

        balance.updated_at = datetime.now(
            timezone.utc
        ).replace(tzinfo=None)

    # ---------------------------------------------------------
    # CREATE REVERSAL STOCK MOVEMENTS
    # ---------------------------------------------------------

    for movement in original_movements:
        db.add(
            StockLedger(
                company_id=movement.company_id,
                document_id=document.id,
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
                movement_date=reversal_date,
            )
        )

    # ---------------------------------------------------------
    # MARK DOCUMENT AS REVERSED
    # ---------------------------------------------------------

    document.status = DocumentStatus.REVERSED

    document.reversed_at = datetime.now(
        timezone.utc
    ).replace(tzinfo=None)

    document.reversed_by = reversed_by

    await db.flush()

    return document