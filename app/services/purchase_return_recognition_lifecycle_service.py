from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.purchase_return_recognition_journal_service import (
    PurchaseReturnRecognitionJournalError,
    generate_and_post_purchase_return_recognition_journal_entry,
    reverse_purchase_return_recognition_journal_entry,
)
from app.services.purchase_return_recognition_persistence_service import (
    PurchaseReturnRecognitionPersistenceError,
)
from app.services.purchase_return_recognition_reconciliation_service import (
    PurchaseReturnRecognitionReconciliationError,
    PurchaseReturnRecognitionReconciliationResult,
    reconcile_purchase_return_recognition_for_fulfillment_line,
)


class PurchaseReturnRecognitionLifecycleError(
    Exception
):
    """
    Purchase Return economic recognition failed during one
    caller-owned transaction.
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


async def _post_created_purchase_return_recognition_events(
    db: AsyncSession,
    *,
    result: PurchaseReturnRecognitionReconciliationResult,
    created_by: int,
) -> None:
    """
    Consume immutable Purchase Return Recognition events in exact
    reconciliation / persistence order.

    Positive original:

        Dr SUPPLIER_PAYABLES
        Cr INVENTORY_GOODS

        GENERAL 291:
        Dr 631 / Cr 281

    Reversal:

        reverse original Purchase Return Recognition JournalEntry
        and bind the new reversal JournalEntry to the immutable
        reversal PurchaseReturnRecognitionEvent.

        GENERAL 291:
        Dr 281 / Cr 631

    Zero-base original/reversal events legitimately produce no JE.

    No INPUT VAT, TaxRecognitionEvent, TaxCreditEvidence, RK,
    supplier-advance reconciliation, or warehouse mutation occurs here.
    """
    for event in result.created_events:
        if event.reversal_of_id is None:
            await (
                generate_and_post_purchase_return_recognition_journal_entry(
                    db,
                    event=event,
                    created_by=created_by,
                )
            )
            continue

        await (
            reverse_purchase_return_recognition_journal_entry(
                db,
                reversal_event=event,
                reversed_by=created_by,
            )
        )


async def reconcile_purchase_return_recognition_lifecycle_for_fulfillment_line(
    db: AsyncSession,
    *,
    company_id: int,
    fulfillment_id: int,
    fulfillment_line_id: int,
    adjustment_date: date,
    created_by: int,
) -> PurchaseReturnRecognitionReconciliationResult:
    """
    Reconcile and account for the economic Purchase Return state of one
    original PURCHASE fulfillment line.

    Flow:

        active PURCHASE TradeReturnEvent history
                    +
        ACTIVE InvoiceFulfillmentAllocation economic capacities
                    ↓
        PurchaseReturnRecognition reconciliation
                    ↓
        immutable PurchaseReturnRecognitionEvent history
                    ↓
        exact-order GL lifecycle

    Accounting layer:

        original:
            Dr 631 / Cr 281

        reversal:
            Dr 281 / Cr 631

    Only returned_base_amount affects GL.

    This service intentionally does NOT:
    - create/reverse warehouse return documents;
    - alter INPUT VAT bridge state;
    - alter TaxRecognitionEvent or TaxCreditEvidence state;
    - create Ukrainian VAT adjustment / RK;
    - reconcile supplier advances;
    - COMMIT or ROLLBACK.

    Caller owns COMMIT / ROLLBACK.
    """
    _validate_context(
        company_id=company_id,
        fulfillment_id=fulfillment_id,
        fulfillment_line_id=fulfillment_line_id,
        adjustment_date=adjustment_date,
        created_by=created_by,
    )

    try:
        result = (
            await reconcile_purchase_return_recognition_for_fulfillment_line(
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
        PurchaseReturnRecognitionPersistenceError,
        PurchaseReturnRecognitionReconciliationError,
    ) as exc:
        raise PurchaseReturnRecognitionLifecycleError(
            "Purchase Return economic recognition "
            "reconciliation failed: "
            f"{exc}"
        ) from exc

    try:
        await _post_created_purchase_return_recognition_events(
            db,
            result=result,
            created_by=created_by,
        )
    except PurchaseReturnRecognitionJournalError as exc:
        raise PurchaseReturnRecognitionLifecycleError(
            "Purchase Return economic recognition "
            "journal posting failed: "
            f"{exc}"
        ) from exc

    return result
