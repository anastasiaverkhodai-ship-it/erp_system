from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.customer_advance_clearing_journal_service import (
    CustomerAdvanceClearingJournalError,
    generate_and_post_customer_advance_clearing_journal_entry,
    reverse_customer_advance_clearing_journal_entry,
)
from app.services.customer_advance_clearing_persistence_service import (
    CustomerAdvanceClearingPersistenceError,
)
from app.services.customer_advance_clearing_reconciliation_service import (
    CustomerAdvanceClearingReconciliationError,
    CustomerAdvanceClearingReconciliationResult,
    reconcile_customer_advance_clearing_for_invoice,
)


class CustomerAdvanceClearingLifecycleError(
    Exception
):
    """Customer Advance Clearing lifecycle failure."""


def _validate_context(
    *,
    company_id: int,
    invoice_id: int,
    adjustment_date: date,
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
            "company_id must be greater than zero"
        )

    if (
        not isinstance(
            invoice_id,
            int,
        )
        or isinstance(
            invoice_id,
            bool,
        )
        or invoice_id <= 0
    ):
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
            "created_by must be greater than zero"
        )


async def _post_created_customer_advance_clearing_events(
    db: AsyncSession,
    *,
    result: CustomerAdvanceClearingReconciliationResult,
    created_by: int,
) -> None:
    """
    Consume immutable CustomerAdvanceClearingEvents in the exact
    persistence order returned by reconciliation.

    Original:
        Dr CUSTOMER_ADVANCES
        Cr CUSTOMER_RECEIVABLES

        GENERAL 291:
        Dr 681
        Cr 361

    Reversal:
        Dr CUSTOMER_RECEIVABLES
        Cr CUSTOMER_ADVANCES

        GENERAL 291:
        Dr 361
        Cr 681

    Reversal-before-replacement ordering is preserved.
    """
    for event in result.created_events:
        if event.reversal_of_id is None:
            await (
                generate_and_post_customer_advance_clearing_journal_entry(
                    db,
                    event=event,
                    created_by=created_by,
                )
            )
        else:
            await (
                reverse_customer_advance_clearing_journal_entry(
                    db,
                    reversal_event=event,
                    reversed_by=created_by,
                )
            )


async def reconcile_customer_advance_clearing_lifecycle_for_invoice(
    db: AsyncSession,
    *,
    company_id: int,
    invoice_id: int,
    adjustment_date: date,
    created_by: int,
) -> CustomerAdvanceClearingReconciliationResult:
    """
    Persist and account for complete customer-advance clearing
    state for one SALES Invoice.

    Commercial capacity:
        ACTIVE RECEIVABLE PaymentSettlementAllocation
        backed by CONFIRMED INCOMING Payment.

    Economic 361 capacity:
        ACTIVE SalesRecognitionEvent
        recognized_gross_amount.

    GL:
        Dr CUSTOMER_ADVANCES
        Cr CUSTOMER_RECEIVABLES

        GENERAL 291:
        Dr 681
        Cr 361

    Tax recognition remains a separate contour.

    Persistence and GL posting intentionally share the
    caller-owned transaction.

    No COMMIT / ROLLBACK occurs here.
    """
    _validate_context(
        company_id=company_id,
        invoice_id=invoice_id,
        adjustment_date=adjustment_date,
        created_by=created_by,
    )

    try:
        result = (
            await reconcile_customer_advance_clearing_for_invoice(
                db,
                company_id=company_id,
                invoice_id=invoice_id,
                adjustment_date=adjustment_date,
                created_by=created_by,
            )
        )
    except (
        CustomerAdvanceClearingReconciliationError,
        CustomerAdvanceClearingPersistenceError,
    ) as exc:
        raise CustomerAdvanceClearingLifecycleError(
            "Customer advance clearing reconciliation failed: "
            f"{exc}"
        ) from exc

    try:
        await _post_created_customer_advance_clearing_events(
            db,
            result=result,
            created_by=created_by,
        )
    except CustomerAdvanceClearingJournalError as exc:
        raise CustomerAdvanceClearingLifecycleError(
            "Customer advance clearing accounting failed: "
            f"{exc}"
        ) from exc

    return result
