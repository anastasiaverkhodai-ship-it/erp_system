from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.sales_return_recognition_journal_service import (
    SalesReturnRecognitionJournalError,
    generate_and_post_sales_return_recognition_journal_entry,
    reverse_sales_return_recognition_journal_entry,
)
from app.services.sales_return_recognition_persistence_service import (
    SalesReturnRecognitionPersistenceError,
)
from app.services.sales_return_recognition_reconciliation_service import (
    SalesReturnRecognitionReconciliationError,
    SalesReturnRecognitionReconciliationResult,
    reconcile_sales_return_recognition_for_fulfillment_line,
)


class SalesReturnRecognitionLifecycleError(
    Exception
):
    """
    Sales Return economic recognition failed during one
    caller-owned business transaction.
    """


def _validate_context(
    *,
    company_id: int,
    fulfillment_id: int,
    fulfillment_line_id: int,
    adjustment_date: date,
    created_by: int,
) -> None:
    if company_id <= 0:
        raise ValueError(
            "company_id must be greater than zero"
        )

    if fulfillment_id <= 0:
        raise ValueError(
            "fulfillment_id must be greater than zero"
        )

    if fulfillment_line_id <= 0:
        raise ValueError(
            "fulfillment_line_id must be greater than zero"
        )

    if not isinstance(
        adjustment_date,
        date,
    ):
        raise TypeError(
            "adjustment_date must be a date"
        )

    if created_by <= 0:
        raise ValueError(
            "created_by must be greater than zero"
        )


async def _post_created_sales_return_recognition_events(
    db: AsyncSession,
    *,
    result: SalesReturnRecognitionReconciliationResult,
    created_by: int,
) -> None:
    """
    Consume immutable Sales Return economic events in exact
    persistence / reconciliation order.

    Original event:
        Dr SALES_DEDUCTIONS
        Cr CUSTOMER_RECEIVABLES

        GENERAL 291:
        Dr 704 / Cr 361

    Reversal event:
        reverse the original Sales Return Recognition JournalEntry
        and bind the new reversal JournalEntry to this immutable
        reversal SalesReturnRecognitionEvent.

    No VAT/RK accounting is performed here.
    """

    for event in result.created_events:
        if event.reversal_of_id is None:
            await (
                generate_and_post_sales_return_recognition_journal_entry(
                    db,
                    event=event,
                    created_by=created_by,
                )
            )

            continue

        await reverse_sales_return_recognition_journal_entry(
            db,
            reversal_event=event,
            reversed_by=created_by,
        )


async def reconcile_sales_return_recognition_lifecycle_for_fulfillment_line(
    db: AsyncSession,
    *,
    company_id: int,
    fulfillment_id: int,
    fulfillment_line_id: int,
    adjustment_date: date,
    created_by: int,
) -> SalesReturnRecognitionReconciliationResult:
    """
    Reconcile and account for the economic Sales Return state
    of one original Sales fulfillment line.

    Flow:

        active TradeReturnEvent history
                    +
        active SalesRecognitionEvent capacity
                    ↓
        SalesReturnRecognition reconciliation
                    ↓
        immutable SalesReturnRecognitionEvent history
                    ↓
        exact-order GL lifecycle

    Accounting layer:

        original:
            Dr 704 / Cr 361

        reversal:
            Dr 361 / Cr 704

    This service intentionally does NOT:
    - create or reverse warehouse return documents;
    - restore inventory cost / COGS;
    - create VAT adjustment / RK events;
    - COMMIT or ROLLBACK the transaction.

    Caller owns COMMIT / ROLLBACK.
    """

    _validate_context(
        company_id=company_id,
        fulfillment_id=fulfillment_id,
        fulfillment_line_id=(
            fulfillment_line_id
        ),
        adjustment_date=adjustment_date,
        created_by=created_by,
    )

    try:
        result = (
            await reconcile_sales_return_recognition_for_fulfillment_line(
                db,
                company_id=company_id,
                fulfillment_id=fulfillment_id,
                fulfillment_line_id=(
                    fulfillment_line_id
                ),
                created_by=created_by,
                adjustment_date=(
                    adjustment_date
                ),
            )
        )
    except (
        SalesReturnRecognitionPersistenceError,
        SalesReturnRecognitionReconciliationError,
    ) as exc:
        raise SalesReturnRecognitionLifecycleError(
            "Sales Return economic recognition "
            "reconciliation failed: "
            f"{exc}"
        ) from exc

    try:
        await _post_created_sales_return_recognition_events(
            db,
            result=result,
            created_by=created_by,
        )
    except SalesReturnRecognitionJournalError as exc:
        raise SalesReturnRecognitionLifecycleError(
            "Sales Return economic recognition "
            "journal posting failed: "
            f"{exc}"
        ) from exc

    return result
