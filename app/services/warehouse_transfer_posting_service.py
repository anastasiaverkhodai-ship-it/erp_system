from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import (
    Document,
    DocumentStatus,
    DocumentType,
)
from app.services.accounting_period_service import (
    ensure_period_open,
)
from app.services.posting_context import (
    create_posting_context,
)
from app.services.warehouse_posting_handler import (
    WarehousePostingHandler,
    WarehousePostingHandlerError,
)


class WarehouseTransferPostingError(
    ValueError
):
    pass


async def post_warehouse_transfer_document(
    db: AsyncSession,
    *,
    document: Document,
    created_by: int,
    exact_receipt_valuation_amounts: (
        dict[int, Decimal] | None
    ) = None,
) -> None:
    """
    Post one internal warehouse-transfer leg.

    This path performs:
    - warehouse quantity posting;
    - stock ledger posting;
    - inventory costing.

    It deliberately does not execute:
    - accounting posting;
    - JournalEntry creation;
    - VAT logic.

    Caller owns commit/rollback.
    """

    if document.id is None:
        raise WarehouseTransferPostingError(
            "transfer document must be persistent"
        )

    if (
        document.status
        != DocumentStatus.DRAFT
    ):
        raise WarehouseTransferPostingError(
            "only draft transfer documents can be posted"
        )

    # Explicitly load the relationship before synchronous
    # attribute access. AsyncSession must never rely on implicit
    # relationship lazy IO.
    await db.refresh(
        document,
        attribute_names=["lines"],
    )

    if not document.lines:
        raise WarehouseTransferPostingError(
            "transfer document has no lines"
        )

    line_ids = {
        line.id
        for line in document.lines
    }

    if None in line_ids:
        raise WarehouseTransferPostingError(
            "all transfer document lines "
            "must be persistent"
        )

    exact_values = dict(
        exact_receipt_valuation_amounts
        or {}
    )

    unknown_ids = (
        set(exact_values)
        - line_ids
    )

    if unknown_ids:
        raise WarehouseTransferPostingError(
            "exact receipt valuation references "
            "a line outside the transfer document"
        )

    normalized_values: dict[
        int,
        Decimal,
    ] = {}

    for (
        line_id,
        raw_value,
    ) in exact_values.items():
        value = Decimal(
            raw_value
        )

        if not value.is_finite():
            raise WarehouseTransferPostingError(
                "exact receipt valuation amount "
                "must be finite"
            )

        if value < Decimal("0"):
            raise WarehouseTransferPostingError(
                "exact receipt valuation amount "
                "cannot be negative"
            )

        normalized_values[
            line_id
        ] = value

    await ensure_period_open(
        company_id=document.company_id,
        operation_date=document.document_date,
        db=db,
    )

    # PostingContext currently requires an accounting rule id.
    # WarehousePostingHandler does not consume it.
    # Sentinel 0 is confined to this transfer-only path.
    context = create_posting_context(
        db=db,
        document=document,
        accounting_rule_id=0,
        created_by=created_by,
    )

    for (
        line_id,
        valuation_amount,
    ) in normalized_values.items():
        context.set_receipt_exact_valuation_amount(
            line_id,
            valuation_amount,
        )

    document.status = (
        DocumentStatus.POSTED
    )

    document.posted_at = datetime.now(
        timezone.utc
    ).replace(
        tzinfo=None
    )

    try:
        await WarehousePostingHandler().post(
            context
        )
    except WarehousePostingHandlerError as exc:
        raise WarehouseTransferPostingError(
            str(exc)
        ) from exc

    await db.flush()


async def reverse_warehouse_transfer_document(
    db: AsyncSession,
    *,
    company_id: int,
    document_id: int,
    reversal_date,
    reversed_by: int,
) -> Document:
    """
    Reverse one internal warehouse-transfer physical leg.

    Warehouse/inventory reversal only.
    No accounting reversal, JournalEntry reversal, VAT,
    commit, or rollback.
    """
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from app.models.stock_ledger import (
        StockLedger,
        StockMovementType,
    )
    from app.services.accounting_period_service import (
        ensure_period_open,
    )
    from app.services.reversal_context import (
        create_reversal_context,
    )
    from app.services.warehouse_reversal_handler import (
        WarehouseReversalHandler,
        WarehouseReversalHandlerError,
    )

    if (
        isinstance(company_id, bool)
        or not isinstance(company_id, int)
        or company_id <= 0
    ):
        raise WarehouseTransferPostingError(
            "company_id must be a positive integer"
        )

    if (
        isinstance(document_id, bool)
        or not isinstance(document_id, int)
        or document_id <= 0
    ):
        raise WarehouseTransferPostingError(
            "document_id must be a positive integer"
        )

    if (
        isinstance(reversed_by, bool)
        or not isinstance(reversed_by, int)
        or reversed_by <= 0
    ):
        raise WarehouseTransferPostingError(
            "reversed_by must be a positive integer"
        )

    result = await db.execute(
        select(Document)
        .options(
            selectinload(
                Document.lines
            )
        )
        .where(
            Document.id == document_id,
            Document.company_id == company_id,
        )
        .with_for_update()
    )

    document = result.scalar_one_or_none()

    if document is None:
        raise WarehouseTransferPostingError(
            "transfer warehouse document not found"
        )

    if (
        document.status
        != DocumentStatus.POSTED
    ):
        raise WarehouseTransferPostingError(
            "only posted transfer warehouse "
            "documents can be reversed"
        )

    if document.document_type not in {
        DocumentType.ISSUE,
        DocumentType.RECEIPT,
    }:
        raise WarehouseTransferPostingError(
            "transfer reversal supports only "
            "ISSUE or RECEIPT documents"
        )

    await ensure_period_open(
        company_id=company_id,
        operation_date=reversal_date,
        db=db,
    )

    movements_result = await db.execute(
        select(StockLedger)
        .where(
            StockLedger.company_id
            == company_id,
            StockLedger.document_id
            == document_id,
            StockLedger.movement_type
            != StockMovementType.REVERSAL,
        )
        .order_by(
            StockLedger.id
        )
    )

    original_movements = tuple(
        movements_result.scalars().all()
    )

    if not original_movements:
        raise WarehouseTransferPostingError(
            "transfer warehouse document "
            "has no stock movements to reverse"
        )

    context = create_reversal_context(
        db=db,
        document=document,
        reversal_date=reversal_date,
        reversed_by=reversed_by,
    )

    context.original_stock_movements = (
        original_movements
    )

    # Internal warehouse transfer never had
    # an accounting journal entry.
    context.original_journal_entry = None

    try:
        await WarehouseReversalHandler().reverse(
            context
        )
    except WarehouseReversalHandlerError as exc:
        raise WarehouseTransferPostingError(
            str(exc)
        ) from exc

    document.status = (
        DocumentStatus.REVERSED
    )
    document.reversed_at = (
        context.reversal_time
    )
    document.reversed_by = (
        context.reversed_by
    )

    await db.flush()

    return document
