from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.supplier_advance_clearing_journal_service import (
    SupplierAdvanceClearingJournalError,
    generate_and_post_supplier_advance_clearing_journal_entry,
    reverse_supplier_advance_clearing_journal_entry,
)
from app.services.supplier_advance_clearing_persistence_service import (
    SupplierAdvanceClearingPersistenceError,
)
from app.services.supplier_advance_clearing_reconciliation_service import (
    SupplierAdvanceClearingReconciliationError,
    SupplierAdvanceClearingReconciliationResult,
    reconcile_supplier_advance_clearing_for_invoice,
)


class SupplierAdvanceClearingLifecycleError(
    Exception
):
    """
    Supplier advance clearing persistence / GL lifecycle failed.
    """


async def _post_created_supplier_advance_clearing_events(
    db: AsyncSession,
    *,
    result: SupplierAdvanceClearingReconciliationResult,
    created_by: int,
) -> None:
    """
    Consume immutable SupplierAdvanceClearingEvents in exact
    persistence order.

    Ordering is authoritative because reconciliation may emit:

        reversal
        replacement original

    for one changed source pair.

    Original:
        Dr SUPPLIER_PAYABLES
        Cr SUPPLIER_ADVANCES

        Dr 631
        Cr 371

    Reversal:
        generic JournalEntry reversal

        Dr 371
        Cr 631
    """

    for event in result.created_events:
        if event.reversal_of_id is None:
            await (
                generate_and_post_supplier_advance_clearing_journal_entry(
                    db,
                    event=event,
                    created_by=created_by,
                )
            )

            continue

        await (
            reverse_supplier_advance_clearing_journal_entry(
                db,
                reversal_event=event,
                reversed_by=created_by,
            )
        )


async def reconcile_supplier_advance_clearing_lifecycle_for_invoice(
    db: AsyncSession,
    *,
    company_id: int,
    invoice_id: int,
    adjustment_date: date,
    created_by: int,
) -> SupplierAdvanceClearingReconciliationResult:
    """
    Persist and account for complete supplier-advance clearing
    state for one PURCHASE Invoice.

    Commercial capacity:
        ACTIVE PAYABLE PaymentSettlementAllocation

    Economic capacity:
        ACTIVE InvoiceFulfillmentAllocation receipt base
        + ACTIVE economic INPUT VAT bridge

    GL:
        Dr SUPPLIER_PAYABLES / Cr SUPPLIER_ADVANCES
        GENERAL 291: Dr 631 / Cr 371

    Persistence and GL posting intentionally share the
    caller-owned transaction.

    No COMMIT / ROLLBACK occurs here.
    """

    if company_id <= 0:
        raise ValueError(
            "company_id must be greater than zero"
        )

    if invoice_id <= 0:
        raise ValueError(
            "invoice_id must be greater than zero"
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

    try:
        result = (
            await reconcile_supplier_advance_clearing_for_invoice(
                db,
                company_id=company_id,
                invoice_id=invoice_id,
                adjustment_date=adjustment_date,
                created_by=created_by,
            )
        )
    except (
        SupplierAdvanceClearingReconciliationError,
        SupplierAdvanceClearingPersistenceError,
    ) as exc:
        raise (
            SupplierAdvanceClearingLifecycleError(
                "Supplier advance clearing "
                "reconciliation failed: "
                f"{exc}"
            )
        ) from exc

    try:
        await (
            _post_created_supplier_advance_clearing_events(
                db,
                result=result,
                created_by=created_by,
            )
        )
    except SupplierAdvanceClearingJournalError as exc:
        raise (
            SupplierAdvanceClearingLifecycleError(
                "Supplier advance clearing "
                "journal posting failed: "
                f"{exc}"
            )
        ) from exc

    return result
