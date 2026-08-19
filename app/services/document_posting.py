
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.document import (
    Document,
    DocumentStatus,
)
from app.models.stock_balance import StockBalance

from app.services.accounting_period_service import ensure_period_open

from app.services.posting_context import (
    create_posting_context,
)

from app.services.posting_engine import (
    PostingEngineError,
)

from app.services.posting_factory import (
    create_default_posting_engine,
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

    context = create_posting_context(
        db=db,
        document=document,
    )

    # ---------------------------------------------------------
    # ACCOUNTING PERIOD
    # ---------------------------------------------------------

    await ensure_period_open(
        company_id=context.company_id,
        operation_date=context.operation_date,
        db=context.db,
    )
    # ---------------------------------------------------------
    # POSTING ENGINE
    # ---------------------------------------------------------

    posting_engine = create_default_posting_engine()

    try:
        await posting_engine.post(
            context
        )
    except PostingEngineError as exc:
        raise DocumentPostingError(
            str(exc)
        ) from exc

    # ---------------------------------------------------------
    # MARK DOCUMENT AS POSTED
    # ---------------------------------------------------------
    # ---------------------------------------------------------
    # MARK DOCUMENT AS POSTED
    # ---------------------------------------------------------

    document.status = DocumentStatus.POSTED
    document.posted_at = context.posting_time

    await db.flush()

    return document