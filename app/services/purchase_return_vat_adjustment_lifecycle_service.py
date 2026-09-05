from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.invoice_fulfillment_allocation import (
    InvoiceFulfillmentAllocation,
)
from app.models.purchase_return_recognition_event import (
    PurchaseReturnRecognitionEvent,
)
from app.services.supplier_advance_clearing_lifecycle_service import (
    SupplierAdvanceClearingLifecycleError,
    reconcile_supplier_advance_clearing_lifecycle_for_invoice,
)

from app.services.purchase_return_vat_adjustment_journal_service import (
    PurchaseReturnVatAdjustmentJournalError,
    generate_and_post_purchase_return_vat_adjustment_journal_entry,
    reverse_purchase_return_vat_adjustment_journal_entry,
)
from app.services.purchase_return_vat_adjustment_reconciliation_service import (
    PurchaseReturnVatAdjustmentReconciliationError,
    PurchaseReturnVatAdjustmentReconciliationResult,
    reconcile_purchase_return_vat_adjustment_for_recognition_event,
)


VALID_BASIS_KINDS = frozenset(
    {
        "goods_received_by_supplier",
        "refund_by_supplier",
    }
)


class PurchaseReturnVatAdjustmentLifecycleError(
    Exception
):
    """
    Purchase Return VAT adjustment reconciliation + GL lifecycle
    failed during one caller-owned transaction.
    """


def _validate_context(
    *,
    company_id: int,
    purchase_return_recognition_event_id: int,
    adjustment_date: date,
    basis_kind: str,
    created_by: int,
) -> None:
    if (
        not isinstance(
            company_id,
            int,
        )
        or isinstance(
            company_id,
            bool,
        )
        or company_id <= 0
    ):
        raise ValueError(
            "company_id must be a positive integer"
        )

    if (
        not isinstance(
            purchase_return_recognition_event_id,
            int,
        )
        or isinstance(
            purchase_return_recognition_event_id,
            bool,
        )
        or purchase_return_recognition_event_id <= 0
    ):
        raise ValueError(
            "purchase_return_recognition_event_id "
            "must be a positive integer"
        )

    if not isinstance(
        adjustment_date,
        date,
    ):
        raise TypeError(
            "adjustment_date must be a date"
        )

    if (
        not isinstance(
            basis_kind,
            str,
        )
        or basis_kind not in VALID_BASIS_KINDS
    ):
        raise ValueError(
            "basis_kind must be one of: "
            "goods_received_by_supplier, "
            "refund_by_supplier"
        )

    if (
        not isinstance(
            created_by,
            int,
        )
        or isinstance(
            created_by,
            bool,
        )
        or created_by <= 0
    ):
        raise ValueError(
            "created_by must be a positive integer"
        )


async def _post_created_purchase_return_vat_adjustment_events(
    db: AsyncSession,
    *,
    result: PurchaseReturnVatAdjustmentReconciliationResult,
    created_by: int,
) -> None:
    """
    Consume immutable PurchaseReturnVatAdjustmentEvent rows in
    exact reconciliation / persistence order.

    Original:
        Dr SUPPLIER_PAYABLES
        Cr VAT_INPUT

    GENERAL 291:
        Dr 631 / Cr 644

    Reversal:
        reverse historical JournalEntry and bind the reversal JE
        to the immutable reversal VAT-adjustment event.

    GENERAL 291:
        Dr 644 / Cr 631

    Zero-tax original/reversal events legitimately produce no JE.

    This layer does NOT:
        alter TaxRecognitionEvent
        alter TaxCreditEvidence
        reconcile supplier advances
        alter warehouse state
        commit
        rollback
    """

    for event in result.created_events:
        if event.reversal_of_id is None:
            await (
                generate_and_post_purchase_return_vat_adjustment_journal_entry(
                    db,
                    event=event,
                    created_by=created_by,
                )
            )

            continue

        await (
            reverse_purchase_return_vat_adjustment_journal_entry(
                db,
                reversal_event=event,
                reversed_by=created_by,
            )
        )


async def _load_affected_purchase_invoice_ids_after_vat_adjustment(
    db: AsyncSession,
    *,
    company_id: int,
    result: PurchaseReturnVatAdjustmentReconciliationResult,
) -> tuple[
    int,
    ...,
]:
    """
    Resolve PURCHASE invoice provenance for immutable PRVAT events
    created by this reconciliation.

    PurchaseReturnVatAdjustmentEvent
        -> PurchaseReturnRecognitionEvent
        -> InvoiceFulfillmentAllocation
        -> invoice_id.

    Supplier clearing reruns only when PRVAT state actually changed.
    """

    source_prre_ids = tuple(
        sorted(
            {
                event.purchase_return_recognition_event_id
                for event
                in result.created_events
            }
        )
    )

    if not source_prre_ids:
        return ()

    for source_prre_id in source_prre_ids:
        if (
            not isinstance(
                source_prre_id,
                int,
            )
            or isinstance(
                source_prre_id,
                bool,
            )
            or source_prre_id <= 0
        ):
            raise (
                PurchaseReturnVatAdjustmentLifecycleError(
                    "Created Purchase Return VAT adjustment "
                    "has invalid PurchaseReturnRecognitionEvent "
                    "provenance"
                )
            )

    rows = (
        await db.execute(
            select(
                PurchaseReturnRecognitionEvent.id,
                InvoiceFulfillmentAllocation.invoice_id,
            )
            .join(
                InvoiceFulfillmentAllocation,
                (
                    InvoiceFulfillmentAllocation.id
                    == PurchaseReturnRecognitionEvent
                    .invoice_fulfillment_allocation_id
                ),
            )
            .where(
                (
                    PurchaseReturnRecognitionEvent.company_id
                    == company_id
                ),
                (
                    InvoiceFulfillmentAllocation.company_id
                    == company_id
                ),
                (
                    PurchaseReturnRecognitionEvent.id.in_(
                        source_prre_ids
                    )
                ),
            )
            .order_by(
                InvoiceFulfillmentAllocation.invoice_id,
                PurchaseReturnRecognitionEvent.id,
            )
        )
    ).all()

    loaded_source_ids = {
        int(
            row[
                0
            ]
        )
        for row in rows
    }

    missing_source_ids = (
        set(
            source_prre_ids
        )
        - loaded_source_ids
    )

    if missing_source_ids:
        raise (
            PurchaseReturnVatAdjustmentLifecycleError(
                "Created Purchase Return VAT adjustment "
                "references missing PurchaseReturnRecognitionEvent: "
                f"{sorted(missing_source_ids)}"
            )
        )

    invoice_ids = tuple(
        sorted(
            {
                int(
                    row[
                        1
                    ]
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
        raise (
            PurchaseReturnVatAdjustmentLifecycleError(
                "Purchase Return VAT adjustment "
                "resolved invalid invoice_id"
            )
        )

    return invoice_ids


async def _reconcile_supplier_advances_after_purchase_return_vat_adjustment(
    db: AsyncSession,
    *,
    company_id: int,
    result: PurchaseReturnVatAdjustmentReconciliationResult,
    adjustment_date: date,
    created_by: int,
) -> None:
    """
    Rebuild Supplier Advance Clearing after economic PRVAT state
    and its Dr631/Cr644 accounting have reached final state.

    Current supplier 631 capacity is reconstructed as:

        allocated posted receipt base
        - ACTIVE PurchaseReturnRecognitionEvent returned_base_amount
        + ACTIVE INPUT VAT fulfillment bridge
        - ACTIVE PurchaseReturnVatAdjustmentEvent adjusted_tax_amount.

    Legal INPUT VAT credit correction Dr644/Cr641 is deliberately
    outside this lifecycle and does not change supplier 631.
    """

    invoice_ids = (
        await _load_affected_purchase_invoice_ids_after_vat_adjustment(
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
            raise (
                PurchaseReturnVatAdjustmentLifecycleError(
                    "Supplier advance clearing after "
                    "Purchase Return VAT adjustment failed: "
                    f"{exc}"
                )
            ) from exc


async def reconcile_purchase_return_vat_adjustment_lifecycle_for_recognition_event(
    db: AsyncSession,
    *,
    company_id: int,
    purchase_return_recognition_event_id: int,
    adjustment_date: date,
    basis_kind: str,
    created_by: int,
) -> PurchaseReturnVatAdjustmentReconciliationResult:
    """
    Persist and account for Purchase Return INPUT VAT adjustment
    state for one immutable PurchaseReturnRecognitionEvent source.

    Flow:
        PRRE + TaxCalculation VAT truth
                ↓
        VAT adjustment reconciliation
                ↓
        immutable PurchaseReturnVatAdjustmentEvent history
                ↓
        exact-order JournalEntry lifecycle

    Persistence and GL share the caller-owned transaction.

    No legal INPUT VAT credit correction occurs here:
        no Dr 644 / Cr 641
        no TaxRecognitionEvent mutation
        no TaxCreditEvidence mutation

    After exact-order PRVAT JournalEntry dispatch, Supplier Advance
    Clearing is reconciled for every affected purchase invoice using
    the final economic 631 capacity including ACTIVE PRVAT tax.

    No COMMIT / ROLLBACK occurs here.
    """

    _validate_context(
        company_id=company_id,
        purchase_return_recognition_event_id=(
            purchase_return_recognition_event_id
        ),
        adjustment_date=adjustment_date,
        basis_kind=basis_kind,
        created_by=created_by,
    )

    try:
        result = (
            await reconcile_purchase_return_vat_adjustment_for_recognition_event(
                db,
                company_id=company_id,
                purchase_return_recognition_event_id=(
                    purchase_return_recognition_event_id
                ),
                adjustment_date=adjustment_date,
                basis_kind=basis_kind,
                created_by=created_by,
            )
        )

    except (
        PurchaseReturnVatAdjustmentReconciliationError
    ) as exc:
        raise (
            PurchaseReturnVatAdjustmentLifecycleError(
                "Purchase Return VAT adjustment "
                "reconciliation failed: "
                f"{exc}"
            )
        ) from exc

    try:
        await (
            _post_created_purchase_return_vat_adjustment_events(
                db,
                result=result,
                created_by=created_by,
            )
        )

    except (
        PurchaseReturnVatAdjustmentJournalError
    ) as exc:
        raise (
            PurchaseReturnVatAdjustmentLifecycleError(
                "Purchase Return VAT adjustment "
                "journal posting failed: "
                f"{exc}"
            )
        ) from exc

    await (
        _reconcile_supplier_advances_after_purchase_return_vat_adjustment(
            db,
            company_id=company_id,
            result=result,
            adjustment_date=adjustment_date,
            created_by=created_by,
        )
    )

    return result
