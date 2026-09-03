from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tax_calculation import TaxCalculation

from app.services.input_vat_fulfillment_bridge_calculation_service import (
    InputVatFulfillmentBridgeDataIntegrityError,
)
from app.services.input_vat_fulfillment_bridge_journal_service import (
    InputVatFulfillmentBridgeJournalError,
    generate_and_post_input_vat_fulfillment_bridge_journal_entry,
    reverse_input_vat_fulfillment_bridge_journal_entry,
)
from app.services.input_vat_fulfillment_bridge_persistence_service import (
    InputVatFulfillmentBridgePersistenceError,
)
from app.services.tax_types import (
    TaxDirection,
    TaxType,
)
from app.services.input_vat_fulfillment_bridge_reconciliation_service import (
    InputVatFulfillmentBridgeReconciliationError,
    InputVatFulfillmentBridgeReconciliationResult,
    reconcile_input_vat_fulfillment_bridge_for_tax_calculation,
)


class InputVatFulfillmentBridgeLifecycleError(
    Exception
):
    """Economic INPUT VAT bridge lifecycle orchestration failed."""


async def _post_created_input_vat_fulfillment_bridge_events(
    db: AsyncSession,
    *,
    result: InputVatFulfillmentBridgeReconciliationResult,
    created_by: int,
) -> None:
    """
    Apply GL effects for newly persisted immutable bridge events.

    created_events is consumed exactly in reconciliation /
    persistence order.

    Original event:

        Dr VAT_INPUT
        Cr SUPPLIER_PAYABLES

        GENERAL:
        Dr 644
        Cr 631

    Reversal event:

        reverse the original JournalEntry and bind the reversal
        JournalEntry to the immutable reversal bridge event.

    Ordering matters for rounding redistribution:

        decreases / reversals
        before
        increases / new originals
    """

    for event in result.created_events:
        if event.reversal_of_id is None:
            await (
                generate_and_post_input_vat_fulfillment_bridge_journal_entry(
                    db,
                    event=event,
                    created_by=created_by,
                )
            )

            continue

        await (
            reverse_input_vat_fulfillment_bridge_journal_entry(
                db,
                reversal_event=event,
                reversed_by=created_by,
            )
        )


async def reconcile_input_vat_fulfillment_bridge_lifecycle_for_tax_calculation(
    db: AsyncSession,
    *,
    company_id: int,
    tax_calculation_id: int,
    adjustment_date: date,
    created_by: int,
) -> InputVatFulfillmentBridgeReconciliationResult:
    """
    Persist and account for the complete economic INPUT VAT
    fulfillment bridge state for one immutable TaxCalculation.

    Persistence and GL posting share the caller-owned transaction.

    If GL posting fails, the caller can roll back both:
        bridge events
        JournalEntries

    No COMMIT / ROLLBACK occurs here.
    """

    try:
        result = (
            await reconcile_input_vat_fulfillment_bridge_for_tax_calculation(
                db,
                company_id=company_id,
                tax_calculation_id=(
                    tax_calculation_id
                ),
                adjustment_date=(
                    adjustment_date
                ),
                created_by=created_by,
            )
        )
    except (
        InputVatFulfillmentBridgeReconciliationError,
        InputVatFulfillmentBridgePersistenceError,
        InputVatFulfillmentBridgeDataIntegrityError,
    ) as exc:
        raise (
            InputVatFulfillmentBridgeLifecycleError(
                "INPUT VAT fulfillment bridge "
                "reconciliation failed: "
                f"{exc}"
            )
        ) from exc

    try:
        await (
            _post_created_input_vat_fulfillment_bridge_events(
                db,
                result=result,
                created_by=created_by,
            )
        )
    except (
        InputVatFulfillmentBridgeJournalError
    ) as exc:
        raise (
            InputVatFulfillmentBridgeLifecycleError(
                "INPUT VAT fulfillment bridge "
                "journal posting failed: "
                f"{exc}"
            )
        ) from exc

    return result

async def reconcile_input_vat_fulfillment_bridge_lifecycle_for_invoice_line(
    db: AsyncSession,
    *,
    company_id: int,
    invoice_id: int,
    invoice_line_id: int,
    adjustment_date: date,
    created_by: int,
) -> tuple[
    InputVatFulfillmentBridgeReconciliationResult,
    ...,
]:
    """
    Reconcile economic INPUT VAT bridge accounting affected by one
    Invoice/Fulfillment allocation mutation.

    Only INPUT VAT TaxCalculations are selected.

    Therefore:
        PURCHASE VAT line -> bridge lifecycle
        SALES line        -> no-op
        non-VAT line      -> no-op

    Caller owns COMMIT / ROLLBACK.
    """

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

    calculation_ids = tuple(
        int(calculation_id)
        for calculation_id
        in (
            await db.execute(
                select(
                    TaxCalculation.id
                )
                .where(
                    TaxCalculation.company_id
                    == company_id,
                    TaxCalculation.trade_document_id
                    == invoice_id,
                    TaxCalculation.trade_document_line_id
                    == invoice_line_id,
                    TaxCalculation.tax_type
                    == TaxType.VAT,
                    TaxCalculation.direction
                    == TaxDirection.INPUT,
                )
                .order_by(
                    TaxCalculation.id
                )
            )
        )
        .scalars()
        .all()
    )

    results = []

    for calculation_id in calculation_ids:
        results.append(
            await (
                reconcile_input_vat_fulfillment_bridge_lifecycle_for_tax_calculation(
                    db,
                    company_id=company_id,
                    tax_calculation_id=(
                        calculation_id
                    ),
                    adjustment_date=(
                        adjustment_date
                    ),
                    created_by=created_by,
                )
            )
        )

    return tuple(
        results
    )
