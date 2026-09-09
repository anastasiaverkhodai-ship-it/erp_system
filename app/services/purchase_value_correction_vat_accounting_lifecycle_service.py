from sqlalchemy.ext.asyncio import AsyncSession

from app.services.purchase_value_correction_vat_adjustment_persistence_service import (
    PurchaseValueCorrectionVatAdjustmentReconciliationResult,
)
from app.services.purchase_value_correction_input_vat_credit_correction_persistence_service import (
    PurchaseValueCorrectionInputVatCreditCorrectionReconciliationResult,
)
from app.services.purchase_value_correction_vat_adjustment_journal_service import (
    PurchaseValueCorrectionVatAdjustmentJournalError,
    generate_and_post_purchase_value_correction_vat_adjustment_journal_entry,
    reverse_purchase_value_correction_vat_adjustment_journal_entry,
)
from app.services.purchase_value_correction_input_vat_credit_correction_journal_service import (
    generate_and_post_purchase_value_correction_input_vat_credit_correction_journal_entry,
    reverse_purchase_value_correction_input_vat_credit_correction_journal_entry,
)

from sqlalchemy import select

from app.models.trade_value_correction_event import (
    TradeValueCorrectionEvent,
)
from app.services.supplier_advance_clearing_lifecycle_service import (
    SupplierAdvanceClearingLifecycleError,
    reconcile_supplier_advance_clearing_lifecycle_for_invoice,
)



class PurchaseValueCorrectionVatAccountingLifecycleError(
    Exception
):
    """PVC economic VAT accounting lifecycle failed."""


async def _load_purchase_value_correction_invoice_id_for_vat_event(
    db: AsyncSession,
    *,
    event,
) -> int:
    """
    Resolve the purchase document directly from immutable PVC source:

        PurchaseValueCorrectionVatAdjustmentEvent
            -> TradeValueCorrectionEvent
            -> trade_document_id.

    Supplier clearing lifecycle performs the authoritative purchase
    invoice validation itself.

    No commit/rollback.
    """

    if (
        not isinstance(
            event.company_id,
            int,
        )
        or isinstance(
            event.company_id,
            bool,
        )
        or event.company_id <= 0
    ):
        raise (
            PurchaseValueCorrectionVatAccountingLifecycleError(
                "PVC VAT event company_id must be positive"
            )
        )

    if (
        not isinstance(
            event.trade_value_correction_event_id,
            int,
        )
        or isinstance(
            event.trade_value_correction_event_id,
            bool,
        )
        or event.trade_value_correction_event_id <= 0
    ):
        raise (
            PurchaseValueCorrectionVatAccountingLifecycleError(
                "PVC VAT TradeValueCorrectionEvent ID "
                "must be positive"
            )
        )

    correction = (
        await db.execute(
            select(
                TradeValueCorrectionEvent
            )
            .where(
                (
                    TradeValueCorrectionEvent.company_id
                    == event.company_id
                ),
                (
                    TradeValueCorrectionEvent.id
                    == event.trade_value_correction_event_id
                ),
            )
            .with_for_update()
        )
    ).scalar_one_or_none()

    if correction is None:
        raise (
            PurchaseValueCorrectionVatAccountingLifecycleError(
                "TradeValueCorrectionEvent for PVC VAT event "
                "was not found"
            )
        )

    if (
        correction.company_id
        != event.company_id
    ):
        raise (
            PurchaseValueCorrectionVatAccountingLifecycleError(
                "PVC VAT and TradeValueCorrectionEvent "
                "company provenance mismatch"
            )
        )

    invoice_id = (
        correction.trade_document_id
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
        raise (
            PurchaseValueCorrectionVatAccountingLifecycleError(
                "TradeValueCorrectionEvent trade_document_id "
                "must be positive"
            )
        )

    return invoice_id


async def post_created_purchase_value_correction_vat_adjustment_journals(
    db: AsyncSession,
    *,
    reconciliation_result: PurchaseValueCorrectionVatAdjustmentReconciliationResult,
    created_by: int,
) -> tuple:
    posted = []

    for event in reconciliation_result.created_events:
        if event.reversal_of_id is None:
            journal = await generate_and_post_purchase_value_correction_vat_adjustment_journal_entry(
                db,
                event=event,
                created_by=created_by,
            )
        else:
            journal = await reverse_purchase_value_correction_vat_adjustment_journal_entry(
                db,
                reversal_event=event,
                reversed_by=created_by,
            )

        if journal is not None:
            posted.append(journal)

    return tuple(posted)


async def post_created_purchase_value_correction_input_vat_credit_correction_journals(
    db: AsyncSession,
    *,
    reconciliation_result: PurchaseValueCorrectionInputVatCreditCorrectionReconciliationResult,
    created_by: int,
) -> tuple:
    posted = []

    for event in reconciliation_result.created_events:
        if event.reversal_of_id is None:
            journal = await generate_and_post_purchase_value_correction_input_vat_credit_correction_journal_entry(
                db,
                event=event,
                created_by=created_by,
            )
        else:
            journal = await reverse_purchase_value_correction_input_vat_credit_correction_journal_entry(
                db,
                reversal_event=event,
                reversed_by=created_by,
            )

        if journal is not None:
            posted.append(journal)

    return tuple(posted)

async def post_created_purchase_value_correction_vat_adjustment_journals_and_reconcile_supplier_clearing(
    db: AsyncSession,
    *,
    reconciliation_result: PurchaseValueCorrectionVatAdjustmentReconciliationResult,
    created_by: int,
) -> tuple:
    """
    Complete ECONOMIC PVC VAT accounting lifecycle.

    Exact ordering:

        1. immutable economic PVC VAT JournalEntry dispatch
           decrease: Dr SUPPLIER_PAYABLES / Cr VAT_INPUT
           increase: Dr VAT_INPUT / Cr SUPPLIER_PAYABLES

        2. rebuild supplier economic 631 capacity

        3. immutable Supplier Advance Clearing reconciliation
           and its JournalEntry dispatch.

    The LEGAL INPUT VAT credit correction lifecycle:

        641 <-> 644

    is deliberately not invoked here and has no effect on 631/371.

    Caller owns commit/rollback.
    """

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

    try:
        journals = (
            await post_created_purchase_value_correction_vat_adjustment_journals(
                db,
                reconciliation_result=reconciliation_result,
                created_by=created_by,
            )
        )
    except PurchaseValueCorrectionVatAdjustmentJournalError as exc:
        raise (
            PurchaseValueCorrectionVatAccountingLifecycleError(
                "PVC economic VAT journal dispatch failed: "
                f"{exc}"
            )
        ) from exc

    created_events = tuple(
        reconciliation_result.created_events
    )

    if not created_events:
        return (
            journals,
            (),
        )

    company_id = None
    invoice_id = None
    adjustment_date = None

    for event in created_events:
        if company_id is None:
            company_id = event.company_id
        elif event.company_id != company_id:
            raise (
                PurchaseValueCorrectionVatAccountingLifecycleError(
                    "PVC VAT reconciliation batch spans companies"
                )
            )

        current_invoice_id = (
            await _load_purchase_value_correction_invoice_id_for_vat_event(
                db,
                event=event,
            )
        )

        if invoice_id is None:
            invoice_id = current_invoice_id
        elif current_invoice_id != invoice_id:
            raise (
                PurchaseValueCorrectionVatAccountingLifecycleError(
                    "PVC VAT reconciliation batch spans invoices"
                )
            )

        if adjustment_date is None:
            adjustment_date = event.adjustment_date
        elif (
            event.adjustment_date
            > adjustment_date
        ):
            adjustment_date = event.adjustment_date

    if (
        company_id is None
        or invoice_id is None
        or adjustment_date is None
    ):
        raise (
            PurchaseValueCorrectionVatAccountingLifecycleError(
                "PVC VAT lifecycle could not resolve "
                "supplier clearing context"
            )
        )

    try:
        clearing_result = (
            await reconcile_supplier_advance_clearing_lifecycle_for_invoice(
                db,
                company_id=company_id,
                invoice_id=invoice_id,
                adjustment_date=adjustment_date,
                created_by=created_by,
            )
        )
    except SupplierAdvanceClearingLifecycleError as exc:
        raise (
            PurchaseValueCorrectionVatAccountingLifecycleError(
                "Supplier advance clearing after PVC "
                "economic VAT adjustment failed: "
                f"{exc}"
            )
        ) from exc

    return (
        journals,
        (clearing_result,),
    )
