from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.account import Account
from app.models.journal_entry import (
    JournalEntry,
    JournalEntryStatus,
)
from app.models.journal_entry_line import JournalEntryLine
from app.services.accounting_period_service import (
    ensure_period_open,
)


class AccountingReversalError(Exception):
    pass


class JournalEntryReversalNotFoundError(
    AccountingReversalError
):
    pass


def _resolve_reversal_tax_recognition_event_id(
    *,
    original_tax_recognition_event_id: int | None,
    override: int | None,
) -> int | None:
    """
    Preserve the original Tax Recognition typed source by default.

    OUTPUT VAT reversal accounting may override it with the
    immutable reversal TaxRecognitionEvent ID.
    """
    if override is None:
        return original_tax_recognition_event_id

    if override <= 0:
        raise AccountingReversalError(
            "Tax recognition event source override "
            "must be greater than zero"
        )

    if original_tax_recognition_event_id is None:
        raise AccountingReversalError(
            "Tax recognition event source override "
            "requires an original Tax Recognition source"
        )

    return override


def _resolve_reversal_sales_recognition_event_id(
    *,
    original_sales_recognition_event_id: int | None,
    override: int | None,
) -> int | None:
    """
    Preserve the original Sales typed source by default.

    Sales Recognition reversal accounting may override it
    with the immutable reversal SalesRecognitionEvent ID.
    """

    if override is None:
        return original_sales_recognition_event_id

    if override <= 0:
        raise AccountingReversalError(
            "Sales recognition event source override "
            "must be greater than zero"
        )

    if original_sales_recognition_event_id is None:
        raise AccountingReversalError(
            "Sales recognition event source override "
            "requires a Sales Recognition original "
            "journal entry"
        )

    return override


def _resolve_reversal_vat_advance_bridge_event_id(
    *,
    original_vat_advance_bridge_event_id: int | None,
    override: int | None,
) -> int | None:
    """
    Preserve the original VAT Advance Bridge typed source
    by default.

    VAT Advance Bridge reversal accounting may override it
    with the immutable reversal VatAdvanceBridgeEvent ID.
    """

    if override is None:
        return original_vat_advance_bridge_event_id

    if override <= 0:
        raise AccountingReversalError(
            "VAT advance bridge event source override "
            "must be greater than zero"
        )

    if original_vat_advance_bridge_event_id is None:
        raise AccountingReversalError(
            "VAT advance bridge event source override "
            "requires a VAT Advance Bridge original "
            "journal entry"
        )

    return override


def _resolve_reversal_input_vat_fulfillment_bridge_event_id(
    *,
    original_input_vat_fulfillment_bridge_event_id: int | None,
    override: int | None,
) -> int | None:
    """
    Preserve the original INPUT VAT Fulfillment Bridge typed source
    by default.

    INPUT VAT Fulfillment Bridge reversal accounting may override it
    with the immutable reversal InputVatFulfillmentBridgeEvent ID.
    """

    if override is None:
        return (
            original_input_vat_fulfillment_bridge_event_id
        )

    if override <= 0:
        raise AccountingReversalError(
            "INPUT VAT fulfillment bridge event "
            "source override must be greater than zero"
        )

    if (
        original_input_vat_fulfillment_bridge_event_id
        is None
    ):
        raise AccountingReversalError(
            "INPUT VAT fulfillment bridge event "
            "source override requires an original "
            "INPUT VAT Fulfillment Bridge journal entry"
        )

    return override


def _resolve_reversal_supplier_advance_clearing_event_id(
    *,
    original_supplier_advance_clearing_event_id: int | None,
    override: int | None,
) -> int | None:
    """
    Preserve the original Supplier Advance Clearing typed
    source by default.

    Reversal accounting may override it with the immutable
    reversal SupplierAdvanceClearingEvent ID.
    """

    if override is None:
        return (
            original_supplier_advance_clearing_event_id
        )

    if override <= 0:
        raise AccountingReversalError(
            "Supplier advance clearing event source "
            "override must be greater than zero"
        )

    if (
        original_supplier_advance_clearing_event_id
        is None
    ):
        raise AccountingReversalError(
            "Supplier advance clearing event source "
            "override requires an original Supplier "
            "Advance Clearing journal entry"
        )

    return override

def _resolve_reversal_customer_advance_clearing_event_id(
    *,
    original_customer_advance_clearing_event_id: int | None,
    override: int | None,
) -> int | None:
    """
    Preserve the original Customer Advance Clearing typed
    source by default.

    Reversal accounting may override it with the immutable
    reversal CustomerAdvanceClearingEvent ID.
    """

    if override is None:
        return (
            original_customer_advance_clearing_event_id
        )

    if override <= 0:
        raise AccountingReversalError(
            "Customer advance clearing event source "
            "override must be greater than zero"
        )

    if (
        original_customer_advance_clearing_event_id
        is None
    ):
        raise AccountingReversalError(
            "Customer advance clearing event source "
            "override requires an original Supplier "
            "Advance Clearing journal entry"
        )

    return override


def _resolve_reversal_sales_return_recognition_event_id(
    *,
    original_sales_return_recognition_event_id: int | None,
    override: int | None,
) -> int | None:
    """
    Preserve the original Sales Return Recognition typed source
    by default.

    Sales Return Recognition reversal accounting may override it
    with the immutable reversal SalesReturnRecognitionEvent ID.
    """

    if override is None:
        return (
            original_sales_return_recognition_event_id
        )

    if override <= 0:
        raise AccountingReversalError(
            "Sales return recognition event source override "
            "must be greater than zero"
        )

    if (
        original_sales_return_recognition_event_id
        is None
    ):
        raise AccountingReversalError(
            "Sales return recognition event source override "
            "requires an original Sales Return Recognition "
            "journal entry"
        )

    return override



def _resolve_reversal_sales_return_cost_restoration_event_id(
    *,
    original_sales_return_cost_restoration_event_id: int | None,
    override: int | None,
) -> int | None:
    """
    Preserve the original Sales Return Cost Restoration typed
    source by default.

    Cost-restoration reversal accounting may override it with
    the immutable reversal SalesReturnCostRestorationEvent ID.
    """

    if override is None:
        return (
            original_sales_return_cost_restoration_event_id
        )

    if override <= 0:
        raise AccountingReversalError(
            "Sales return cost restoration event source "
            "override must be greater than zero"
        )

    if (
        original_sales_return_cost_restoration_event_id
        is None
    ):
        raise AccountingReversalError(
            "Sales return cost restoration event source "
            "override requires an original Sales Return "
            "Cost Restoration journal entry"
        )

    return override


def _resolve_reversal_purchase_return_recognition_event_id(
    *,
    original_purchase_return_recognition_event_id: int | None,
    override: int | None,
) -> int | None:
    """
    Preserve the original Purchase Return Recognition typed
    source by default.

    Purchase Return Recognition reversal accounting may
    override it with the immutable reversal
    PurchaseReturnRecognitionEvent ID.
    """
    if override is None:
        return (
            original_purchase_return_recognition_event_id
        )

    if override <= 0:
        raise AccountingReversalError(
            "Purchase return recognition event source "
            "override must be greater than zero"
        )

    if (
        original_purchase_return_recognition_event_id
        is None
    ):
        raise AccountingReversalError(
            "Purchase return recognition event source "
            "override requires an original Purchase "
            "Return Recognition journal entry"
        )

    return override

def _resolve_reversal_purchase_return_vat_adjustment_event_id(
    *,
    original_purchase_return_vat_adjustment_event_id: int | None,
    override: int | None,
) -> int | None:
    if override is None:
        return original_purchase_return_vat_adjustment_event_id

    if override <= 0:
        raise AccountingReversalError(
            "Purchase Return VAT adjustment event "
            "source override must be greater than zero"
        )

    if (
        original_purchase_return_vat_adjustment_event_id
        is None
    ):
        raise AccountingReversalError(
            "Purchase Return VAT adjustment event "
            "source override requires an original "
            "Purchase Return VAT adjustment journal entry"
        )

    return override


def _resolve_reversal_purchase_return_input_vat_credit_correction_event_id(
    *,
    original_purchase_return_input_vat_credit_correction_event_id: (
        int | None
    ),
    override_purchase_return_input_vat_credit_correction_event_id: (
        int | None
    ),
) -> int | None:
    if (
        override_purchase_return_input_vat_credit_correction_event_id
        is None
    ):
        return (
            original_purchase_return_input_vat_credit_correction_event_id
        )

    if (
        override_purchase_return_input_vat_credit_correction_event_id
        <= 0
    ):
        raise AccountingReversalError(
            "Purchase Return INPUT VAT credit correction "
            "source override must be greater than zero"
        )

    if (
        original_purchase_return_input_vat_credit_correction_event_id
        is None
    ):
        raise AccountingReversalError(
            "Purchase Return INPUT VAT credit correction "
            "source override requires an original "
            "JournalEntry with that typed business source"
        )

    return (
        override_purchase_return_input_vat_credit_correction_event_id
    )


async def reverse_journal_entry(
    db: AsyncSession,
    company_id: int,
    journal_entry_id: int,
    reversal_date: date,
    reversed_by: int,
    tax_recognition_event_id_override: int | None = None,
    sales_recognition_event_id_override: int | None = None,
    vat_advance_bridge_event_id_override: int | None = None,
    input_vat_fulfillment_bridge_event_id_override: int | None = None,
    supplier_advance_clearing_event_id_override: int | None = None,
    customer_advance_clearing_event_id_override: int | None = None,
    sales_return_recognition_event_id_override: int | None = None,
    sales_return_cost_restoration_event_id_override: int | None = None,
    purchase_return_recognition_event_id_override: int | None = None,
    purchase_return_vat_adjustment_event_id_override: int | None = None,
    purchase_return_input_vat_credit_correction_event_id_override: int | None = None,
    purchase_value_correction_vat_adjustment_event_id_override: int | None = None,
    purchase_value_correction_input_vat_credit_correction_event_id_override: int | None = None,
    purchase_value_correction_fifo_impact_event_id_override: int | None = None,
    purchase_value_correction_ma_replay_event_id_override: int | None = None,
    landed_cost_valuation_event_id: int | None = None,
) -> JournalEntry:
    result = await db.execute(
        select(JournalEntry)
        .options(
            selectinload(JournalEntry.lines)
        )
        .where(
            JournalEntry.id == journal_entry_id,
            JournalEntry.company_id == company_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )

    original_entry = result.scalar_one_or_none()
    if original_entry is not None and getattr(original_entry,'tax_invoice_correction_line_id',None):
        raise AccountingReversalError('RK VAT posting cannot be reversed directly; use a documented tax correction')

    if original_entry is None:
        raise JournalEntryReversalNotFoundError(
            "Journal entry not found"
        )

    if getattr(original_entry,'payment_settlement_allocation_id',None):
        from app.models.payment_settlement_allocation import PaymentSettlementAllocation
        from app.models.counterparty_open_item import CounterpartyOpenItem
        opening_item=await db.scalar(select(CounterpartyOpenItem.id).join(PaymentSettlementAllocation,
            (PaymentSettlementAllocation.company_id==CounterpartyOpenItem.company_id)
            & (PaymentSettlementAllocation.open_item_id==CounterpartyOpenItem.id)).where(
                PaymentSettlementAllocation.company_id==company_id,
                PaymentSettlementAllocation.id==original_entry.payment_settlement_allocation_id,
                CounterpartyOpenItem.opening_balance_id.is_not(None)))
        if opening_item is not None and db.info.get('opening_settlement_reversal')!=original_entry.payment_settlement_allocation_id:
            raise AccountingReversalError('Reverse opening settlement through its allocation lifecycle')

    closing_id = getattr(original_entry, "year_end_closing_id", None)
    if closing_id is not None and db.info.get("year_end_closing_active") != closing_id:
        raise AccountingReversalError("Reverse year-end journals through their closing lifecycle")

    if (
        original_entry.status
        != JournalEntryStatus.POSTED
    ):
        raise AccountingReversalError(
            "Only posted journal entries can be reversed"
        )

    if original_entry.reversal_of_id is not None:
        raise AccountingReversalError(
            "A reversal journal entry cannot be reversed"
        )

    if getattr(original_entry, "opening_balance_id", None):
        from app.models.opening_balance_detail import OpeningBalanceDetail
        detail=await db.scalar(select(OpeningBalanceDetail.id).where(
            OpeningBalanceDetail.company_id==company_id,
            OpeningBalanceDetail.opening_balance_id==original_entry.opening_balance_id))
        if detail is not None and db.info.get('opening_detail_lifecycle')!=original_entry.opening_balance_id:
            raise AccountingReversalError('Reverse detailed opening through its opening lifecycle')
        if reversed_by <= 0 or not original_entry.entry_date <= reversal_date <= date.today():
            raise AccountingReversalError("Invalid opening reversal actor/date")

    if not original_entry.lines:
        raise AccountingReversalError(
            "Journal entry has no lines to reverse"
        )

    from app.models.purchase_landed_cost_valuation_event import PurchaseLandedCostValuationEvent
    from app.services.purchase_landed_cost_capitalization_service import has_capitalized_expenses_for_journal
    linked_valuation = (await db.execute(select(PurchaseLandedCostValuationEvent.id).where(
        PurchaseLandedCostValuationEvent.company_id == company_id,
        PurchaseLandedCostValuationEvent.journal_entry_id == original_entry.id,
    ))).scalar_one_or_none()
    if linked_valuation is not None and linked_valuation != landed_cost_valuation_event_id:
        raise AccountingReversalError("Reverse landed-cost valuation through its lifecycle")
    if await has_capitalized_expenses_for_journal(db, company_id=company_id, journal_entry_id=original_entry.id):
        raise AccountingReversalError("Expense has active capitalized landed costs; reverse them first")

    if original_entry.document_id is not None:
        from app.services.landed_cost_inventory_lifecycle import inventory_operation_active
        if not inventory_operation_active(db, company_id):
            from app.services.purchase_landed_cost_persistence_service import has_active_purchase_landed_costs
            from app.models.inventory_cost_entry import InventoryCostEntry
            from sqlalchemy.orm import aliased
            reversed_valuation = aliased(PurchaseLandedCostValuationEvent)
            active_issue_cost = await db.scalar(select(PurchaseLandedCostValuationEvent.id)
                .join(InventoryCostEntry, InventoryCostEntry.id == PurchaseLandedCostValuationEvent.inventory_cost_entry_id)
                .where(PurchaseLandedCostValuationEvent.company_id == company_id,
                       InventoryCostEntry.company_id == company_id,
                       InventoryCostEntry.document_id == original_entry.document_id,
                       PurchaseLandedCostValuationEvent.reversal_of_id.is_(None),
                       ~select(reversed_valuation.id).where(
                           reversed_valuation.reversal_of_id == PurchaseLandedCostValuationEvent.id).exists())
                .limit(1))
            if active_issue_cost is not None or await has_active_purchase_landed_costs(
                db, company_id=company_id, warehouse_document_id=original_entry.document_id,
            ):
                raise AccountingReversalError("Reverse the warehouse document through its inventory lifecycle to reconcile landed costs")

    await ensure_period_open(
        company_id=company_id,
        operation_date=reversal_date,
        db=db,
    )

    existing_reversal_result = await db.execute(
        select(JournalEntry.id).where(
            JournalEntry.reversal_of_id
            == original_entry.id
        )
    )

    if (
        existing_reversal_result.scalar_one_or_none()
        is not None
    ):
        raise AccountingReversalError(
            "Journal entry has already been reversed"
        )

    account_ids = {
        line.account_id
        for line in original_entry.lines
    }

    accounts_result = await db.execute(
        select(Account.id).where(
            Account.id.in_(account_ids),
            Account.company_id == company_id,
        )
    )

    valid_account_ids = set(
        accounts_result.scalars().all()
    )

    invalid_account_ids = (
        account_ids - valid_account_ids
    )

    if invalid_account_ids:
        raise AccountingReversalError(
            (
                "Journal entry contains accounts "
                "that do not belong to this company: "
                f"{sorted(invalid_account_ids)}"
            )
        )

    reversal_entry = JournalEntry(
        year_end_closing_id=getattr(original_entry, "year_end_closing_id", None),
        opening_balance_id=getattr(original_entry, "opening_balance_id", None),
        company_id=company_id,
        document_id=original_entry.document_id,
        payment_id=original_entry.payment_id,
        payment_settlement_allocation_id=(
            original_entry.payment_settlement_allocation_id
        ),
        tax_recognition_event_id=(
            _resolve_reversal_tax_recognition_event_id(
                original_tax_recognition_event_id=(
                    original_entry.tax_recognition_event_id
                ),
                override=(
                    tax_recognition_event_id_override
                ),
            )
        ),
        sales_recognition_event_id=(
            _resolve_reversal_sales_recognition_event_id(
                original_sales_recognition_event_id=(
                    original_entry.sales_recognition_event_id
                ),
                override=(
                    sales_recognition_event_id_override
                ),
            )
        ),
        vat_advance_bridge_event_id=(
            _resolve_reversal_vat_advance_bridge_event_id(
                original_vat_advance_bridge_event_id=(
                    original_entry.vat_advance_bridge_event_id
                ),
                override=(
                    vat_advance_bridge_event_id_override
                ),
            )
        ),
        input_vat_fulfillment_bridge_event_id=(
            _resolve_reversal_input_vat_fulfillment_bridge_event_id(
                original_input_vat_fulfillment_bridge_event_id=(
                    original_entry
                    .input_vat_fulfillment_bridge_event_id
                ),
                override=(
                    input_vat_fulfillment_bridge_event_id_override
                ),
            )
        ),
        supplier_advance_clearing_event_id=(
            _resolve_reversal_supplier_advance_clearing_event_id(
                original_supplier_advance_clearing_event_id=(
                    original_entry
                    .supplier_advance_clearing_event_id
                ),
                override=(
                    supplier_advance_clearing_event_id_override
                ),
            )
        ),
        customer_advance_clearing_event_id=(
            _resolve_reversal_customer_advance_clearing_event_id(
                original_customer_advance_clearing_event_id=(
                    original_entry
                    .customer_advance_clearing_event_id
                ),
                override=(
                    customer_advance_clearing_event_id_override
                ),
            )
        ),
        sales_return_recognition_event_id=(
            _resolve_reversal_sales_return_recognition_event_id(
                original_sales_return_recognition_event_id=(
                    getattr(
                        original_entry,
                        "sales_return_recognition_event_id",
                        None,
                    )
                ),
                override=(
                    sales_return_recognition_event_id_override
                ),
            )
        ),
        sales_return_cost_restoration_event_id=(
            _resolve_reversal_sales_return_cost_restoration_event_id(
                original_sales_return_cost_restoration_event_id=(
                    getattr(
                        original_entry,
                        "sales_return_cost_restoration_event_id",
                        None,
                    )
                ),
                override=(
                    sales_return_cost_restoration_event_id_override
                ),
            )
        ),
        purchase_return_recognition_event_id=(
            _resolve_reversal_purchase_return_recognition_event_id(
                original_purchase_return_recognition_event_id=(
                    getattr(
                        original_entry,
                        "purchase_return_recognition_event_id",
                        None,
                    )
                ),
                override=(
                    purchase_return_recognition_event_id_override
                ),
            )
        ),
        purchase_return_vat_adjustment_event_id=(
            _resolve_reversal_purchase_return_vat_adjustment_event_id(
                original_purchase_return_vat_adjustment_event_id=(
                    getattr(
                        original_entry,
                        "purchase_return_vat_adjustment_event_id",
                        None,
                    )
                ),
                override=(
                    purchase_return_vat_adjustment_event_id_override
                ),
            )
        ),
        purchase_return_input_vat_credit_correction_event_id=(
            _resolve_reversal_purchase_return_input_vat_credit_correction_event_id(
                original_purchase_return_input_vat_credit_correction_event_id=(
                    original_entry
                    .purchase_return_input_vat_credit_correction_event_id
                ),
                override_purchase_return_input_vat_credit_correction_event_id=(
                    purchase_return_input_vat_credit_correction_event_id_override
                ),
            )
        ),
        purchase_value_correction_vat_adjustment_event_id=(
            purchase_value_correction_vat_adjustment_event_id_override
            if (
                purchase_value_correction_vat_adjustment_event_id_override
                is not None
            )
            else getattr(
                original_entry,
                "purchase_value_correction_vat_adjustment_event_id",
                None,
            )
        ),
        purchase_value_correction_input_vat_credit_correction_event_id=(
            purchase_value_correction_input_vat_credit_correction_event_id_override
            if (
                purchase_value_correction_input_vat_credit_correction_event_id_override
                is not None
            )
            else getattr(
                original_entry,
                (
                    "purchase_value_correction_input_vat_"
                    "credit_correction_event_id"
                ),
                None,
            )
        ),
        purchase_value_correction_fifo_impact_event_id=(
            purchase_value_correction_fifo_impact_event_id_override
            if (
                purchase_value_correction_fifo_impact_event_id_override
                is not None
            )
            else getattr(
                original_entry,
                "purchase_value_correction_fifo_impact_event_id",
                None,
            )
        ),
        purchase_value_correction_ma_replay_event_id=(
            purchase_value_correction_ma_replay_event_id_override
            if (
                purchase_value_correction_ma_replay_event_id_override
                is not None
            )
            else getattr(
                original_entry,
                "purchase_value_correction_ma_replay_event_id",
                None,
            )
        ),
        accounting_rule_id=original_entry.accounting_rule_id,
        entry_date=reversal_date,
        description=(
            f"Reversal of JournalEntry "
            f"{original_entry.id}"
        ),
        status=JournalEntryStatus.POSTED,
        created_by=reversed_by,
        posted_at=datetime.utcnow(),
        reversal_of_id=original_entry.id,
    )

    reversal_entry.lines = [
        JournalEntryLine(
            line_no=line.line_no,
            account_id=line.account_id,
            debit=line.credit,
            credit=line.debit,
            description=(
                f"Reversal of line {line.line_no}"
            ),
        )
        for line in original_entry.lines
    ]

    db.add(reversal_entry)

    original_entry.status = (
        JournalEntryStatus.REVERSED
    )
    original_entry.reversed_at = datetime.utcnow()
    original_entry.reversed_by = reversed_by

    await db.flush()

    return reversal_entry
