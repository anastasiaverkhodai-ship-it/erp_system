from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.invoice_fulfillment_allocation import (
    InvoiceFulfillmentAllocation,
)
from app.services.purchase_return_recognition_journal_service import (
    PurchaseReturnRecognitionJournalError,
    generate_and_post_purchase_return_recognition_journal_entry,
    reverse_purchase_return_recognition_journal_entry,
)
from app.services.purchase_return_recognition_persistence_service import (
    PurchaseReturnRecognitionPersistenceError,
)
from app.services.supplier_advance_clearing_lifecycle_service import (
    SupplierAdvanceClearingLifecycleError,
    reconcile_supplier_advance_clearing_lifecycle_for_invoice,
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


async def _load_affected_purchase_invoice_ids(
    db: AsyncSession,
    *,
    company_id: int,
    result: PurchaseReturnRecognitionReconciliationResult,
) -> tuple[
    int,
    ...,
]:
    """
    Resolve PURCHASE Invoice provenance for the exact immutable
    PurchaseReturnRecognitionEvents created by this reconciliation.

    Supplier-advance reconciliation only needs to rerun when PRRE state
    changed. Every created original/reversal/replacement event carries the
    authoritative InvoiceFulfillmentAllocation source identity.
    """
    source_ids = tuple(
        sorted(
            {
                event.invoice_fulfillment_allocation_id
                for event
                in result.created_events
            }
        )
    )

    if not source_ids:
        return ()

    for source_id in source_ids:
        if (
            not isinstance(
                source_id,
                int,
            )
            or isinstance(
                source_id,
                bool,
            )
            or source_id <= 0
        ):
            raise PurchaseReturnRecognitionLifecycleError(
                "Created Purchase Return Recognition event "
                "has invalid InvoiceFulfillmentAllocation provenance"
            )

    rows = (
        await db.execute(
            select(
                InvoiceFulfillmentAllocation.id,
                InvoiceFulfillmentAllocation.invoice_id,
            )
            .where(
                (
                    InvoiceFulfillmentAllocation.company_id
                    == company_id
                ),
                (
                    InvoiceFulfillmentAllocation.id.in_(
                        source_ids
                    )
                ),
            )
            .order_by(
                InvoiceFulfillmentAllocation.invoice_id,
                InvoiceFulfillmentAllocation.id,
            )
        )
    ).all()

    loaded_source_ids = {
        int(
            row[0]
        )
        for row in rows
    }

    missing_source_ids = (
        set(
            source_ids
        )
        - loaded_source_ids
    )

    if missing_source_ids:
        raise PurchaseReturnRecognitionLifecycleError(
            "Created Purchase Return Recognition event "
            "references missing InvoiceFulfillmentAllocation: "
            f"{sorted(missing_source_ids)}"
        )

    invoice_ids = tuple(
        sorted(
            {
                int(
                    row[1]
                )
                for row in rows
            }
        )
    )

    if any(
        invoice_id <= 0
        for invoice_id
        in invoice_ids
    ):
        raise PurchaseReturnRecognitionLifecycleError(
            "Purchase Return Recognition resolved invalid invoice_id"
        )

    return invoice_ids


async def _reconcile_supplier_advances_after_purchase_return(
    db: AsyncSession,
    *,
    company_id: int,
    result: PurchaseReturnRecognitionReconciliationResult,
    adjustment_date: date,
    created_by: int,
) -> None:
    """
    Rebuild Supplier Advance Clearing only after Purchase Return economic
    recognition and its Dr631/Cr281 accounting have reached final state.

    Current 631 capacity is reconstructed by Supplier Advance Clearing as:

        posted receipt base
        - ACTIVE PurchaseReturnRecognitionEvent returned_base_amount
        + current ACTIVE INPUT VAT bridge.

    VAT/RK itself is not mutated here.
    """
    invoice_ids = (
        await _load_affected_purchase_invoice_ids(
            db,
            company_id=company_id,
            result=result,
        )
    )

    for invoice_id in invoice_ids:
        try:
            await (
                reconcile_supplier_advance_clearing_lifecycle_for_invoice(
                    db,
                    company_id=company_id,
                    invoice_id=invoice_id,
                    adjustment_date=adjustment_date,
                    created_by=created_by,
                )
            )
        except SupplierAdvanceClearingLifecycleError as exc:
            raise PurchaseReturnRecognitionLifecycleError(
                "Supplier advance clearing after Purchase Return failed: "
                f"{exc}"
            ) from exc


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
                    ↓
        Supplier Advance Clearing reconciliation for affected invoice(s)

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
    - alter INPUT VAT / RK tax state during supplier reconciliation;
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

    await _reconcile_supplier_advances_after_purchase_return(
        db,
        company_id=company_id,
        result=result,
        adjustment_date=adjustment_date,
        created_by=created_by,
    )

    return result
