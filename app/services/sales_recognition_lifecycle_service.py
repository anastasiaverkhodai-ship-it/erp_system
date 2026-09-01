from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.trade_document import TradeDocument

from app.services.sales_recognition_calculation_service import (
    SalesRecognitionDataIntegrityError,
)
from app.services.sales_recognition_journal_service import (
    SalesRecognitionJournalError,
    generate_and_post_sales_recognition_journal_entry,
    reverse_sales_recognition_journal_entry,
)
from app.services.sales_recognition_persistence_service import (
    SalesRecognitionPersistenceError,
)
from app.services.sales_recognition_reconciliation_service import (
    SalesRecognitionReconciliationError,
    SalesRecognitionReconciliationResult,
    reconcile_sales_recognition_for_invoice_line,
)
from app.services.trade_document_types import (
    TradeDirection,
)


class SalesRecognitionLifecycleError(Exception):
    """Sales recognition failed during a business lifecycle mutation."""


def _validate_context(
    *,
    company_id: int,
    invoice_id: int,
    invoice_line_id: int,
    adjustment_date: date,
    created_by: int,
) -> None:
    if company_id <= 0:
        raise ValueError(
            "company_id must be greater than zero"
        )

    if invoice_id <= 0:
        raise ValueError(
            "invoice_id must be greater than zero"
        )

    if invoice_line_id <= 0:
        raise ValueError(
            "invoice_line_id must be greater than zero"
        )

    if not isinstance(
        adjustment_date,
        date,
    ):
        raise ValueError(
            "adjustment_date must be a date"
        )

    if created_by <= 0:
        raise ValueError(
            "created_by must be greater than zero"
        )


async def _get_invoice_direction(
    db: AsyncSession,
    *,
    company_id: int,
    invoice_id: int,
) -> TradeDirection:
    direction = (
        await db.execute(
            select(
                TradeDocument.direction
            ).where(
                TradeDocument.company_id
                == company_id,
                TradeDocument.id
                == invoice_id,
            )
        )
    ).scalar_one_or_none()

    if direction is None:
        raise SalesRecognitionLifecycleError(
            "Trade Invoice not found during "
            "Sales recognition lifecycle"
        )

    try:
        return TradeDirection(
            direction
        )
    except ValueError as exc:
        raise SalesRecognitionLifecycleError(
            "Trade Invoice has unsupported "
            "direction during Sales recognition"
        ) from exc


async def _post_created_sales_recognition_events(
    db: AsyncSession,
    *,
    result: SalesRecognitionReconciliationResult,
    created_by: int,
) -> None:
    """
    Post GL effects for newly persisted immutable Sales events.

    created_events is consumed exactly in reconciliation /
    persistence order.

    Original event:
        Dr CUSTOMER_RECEIVABLES / Cr GOODS_REVENUE

    Reversal event:
        reverse the original Sales Recognition JournalEntry
        and bind the reversal JE to this reversal event.
    """

    for event in result.created_events:
        if event.reversal_of_id is None:
            await generate_and_post_sales_recognition_journal_entry(
                db,
                event=event,
                created_by=created_by,
            )
            continue

        await reverse_sales_recognition_journal_entry(
            db,
            reversal_event=event,
            reversed_by=created_by,
        )


async def reconcile_sales_recognition_lifecycle_for_invoice_line(
    db: AsyncSession,
    *,
    company_id: int,
    invoice_id: int,
    invoice_line_id: int,
    adjustment_date: date,
    created_by: int,
) -> SalesRecognitionReconciliationResult | None:
    """
    Reconcile commercial Sales recognition affected by one
    InvoiceFulfillmentAllocation lifecycle mutation.

    SALE:
        1. reconcile immutable commercial Sales events;
        2. post their GL effects in exact created-event order.

    PURCHASE:
        no commercial Sales recognition side effect.

    OUTPUT VAT remains a separate lifecycle step owned by the
    InvoiceFulfillmentAllocation caller and runs only after this
    Sales lifecycle returns successfully.

    Caller owns COMMIT / ROLLBACK.
    """

    _validate_context(
        company_id=company_id,
        invoice_id=invoice_id,
        invoice_line_id=invoice_line_id,
        adjustment_date=adjustment_date,
        created_by=created_by,
    )

    direction = await _get_invoice_direction(
        db,
        company_id=company_id,
        invoice_id=invoice_id,
    )

    if (
        direction
        != TradeDirection.SALE
    ):
        return None

    try:
        result = (
            await reconcile_sales_recognition_for_invoice_line(
                db,
                company_id=company_id,
                invoice_id=invoice_id,
                invoice_line_id=invoice_line_id,
                adjustment_date=adjustment_date,
                created_by=created_by,
            )
        )
    except (
        SalesRecognitionPersistenceError,
        SalesRecognitionReconciliationError,
        SalesRecognitionDataIntegrityError,
    ) as exc:
        raise SalesRecognitionLifecycleError(
            "Commercial Sales recognition "
            "reconciliation failed: "
            f"{exc}"
        ) from exc

    try:
        await _post_created_sales_recognition_events(
            db,
            result=result,
            created_by=created_by,
        )
    except SalesRecognitionJournalError as exc:
        raise SalesRecognitionLifecycleError(
            "Commercial Sales recognition "
            "journal posting failed: "
            f"{exc}"
        ) from exc

    return result
