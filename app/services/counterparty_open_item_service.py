from datetime import timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.counterparty_open_item import (
    CounterpartyOpenItem,
)
from app.models.trade_document import TradeDocument
from app.services.counterparty_open_item_types import (
    CounterpartyOpenItemStatus,
    CounterpartyOpenItemType,
)
from app.services.invoice_tax_calculation_service import (
    InvoiceTaxCalculationError,
    calculate_invoice_payable_total,
)
from app.services.money_rounding import (
    round_currency_amount,
)
from app.services.trade_document_types import (
    TradeDirection,
    TradeDocumentKind,
    TradeDocumentStatus,
)


class CounterpartyOpenItemError(Exception):
    """Base AR/AP open-item persistence error."""


class CounterpartyOpenItemSourceTypeError(
    CounterpartyOpenItemError
):
    """Source document is not a valid Trade Invoice."""


class CounterpartyOpenItemSourceStatusError(
    CounterpartyOpenItemError
):
    """Source invoice is not in the required lifecycle state."""


class CounterpartyOpenItemAmountError(
    CounterpartyOpenItemError
):
    """Invoice obligation amount is invalid."""


class CounterpartyOpenItemNotFoundError(
    CounterpartyOpenItemError
):
    """Expected AR/AP open item does not exist."""


class CounterpartyOpenItemStateError(
    CounterpartyOpenItemError
):
    """AR/AP open item is in an invalid lifecycle state."""


def get_open_item_type_for_invoice(
    document: TradeDocument,
) -> CounterpartyOpenItemType:
    """
    Map Trade Invoice direction to AR/AP obligation type.

    SALE     -> RECEIVABLE
    PURCHASE -> PAYABLE
    """
    if document.kind != TradeDocumentKind.INVOICE:
        raise CounterpartyOpenItemSourceTypeError(
            "Only trade document kind 'invoice' "
            "can create a counterparty open item"
        )

    if document.direction == TradeDirection.SALE:
        return (
            CounterpartyOpenItemType.RECEIVABLE
        )

    if (
        document.direction
        == TradeDirection.PURCHASE
    ):
        return (
            CounterpartyOpenItemType.PAYABLE
        )

    raise CounterpartyOpenItemSourceTypeError(
        "Unsupported Trade Invoice direction"
    )


def calculate_invoice_open_item_amount(
    document: TradeDocument,
) -> Decimal:
    """
    Calculate immutable tax-inclusive AR/AP obligation.

    VAT-exclusive invoice lines add VAT on top.
    VAT-inclusive invoice lines already contain VAT.
    Lines without tax configuration preserve their
    existing commercial amount behavior.
    """

    if document.kind != TradeDocumentKind.INVOICE:
        raise CounterpartyOpenItemSourceTypeError(
            "Only Trade Invoice can create "
            "an AR/AP obligation"
        )

    try:
        amount = calculate_invoice_payable_total(
            document
        )
    except InvoiceTaxCalculationError as exc:
        raise CounterpartyOpenItemAmountError(
            str(exc)
        ) from exc

    if amount <= Decimal("0"):
        raise CounterpartyOpenItemAmountError(
            "Trade Invoice obligation amount "
            "must be greater than zero"
        )

    return amount

def calculate_invoice_due_date(
    document: TradeDocument,
):
    """
    Invoice due date is document date plus payment terms.

    TradeDocument already guarantees payment_term_days >= 0.
    """
    if document.payment_term_days < 0:
        raise CounterpartyOpenItemStateError(
            "Invoice payment term cannot be negative"
        )

    return (
        document.document_date
        + timedelta(
            days=document.payment_term_days
        )
    )


async def create_counterparty_open_item_for_invoice(
    db: AsyncSession,
    *,
    document: TradeDocument,
) -> CounterpartyOpenItem:
    """
    Persist exactly one AR/AP obligation for a confirmed invoice.

    Caller must hold the TradeDocument header lock.

    Concurrency protection is two-layered:
        1. invoice confirmation locks TradeDocument FOR UPDATE
        2. DB UNIQUE(company_id, trade_document_id)

    Caller owns COMMIT / ROLLBACK.
    """
    if (
        document.status
        != TradeDocumentStatus.CONFIRMED
    ):
        raise CounterpartyOpenItemSourceStatusError(
            "Only confirmed Trade Invoice can create "
            "a counterparty open item"
        )

    item_type = (
        get_open_item_type_for_invoice(
            document
        )
    )

    amount = (
        calculate_invoice_open_item_amount(
            document
        )
    )

    due_date = calculate_invoice_due_date(
        document
    )

    item = CounterpartyOpenItem(
        company_id=document.company_id,
        trade_document_id=document.id,
        counterparty_id=document.counterparty_id,
        contract_id=document.contract_id,
        item_type=item_type,
        status=(
            CounterpartyOpenItemStatus.OPEN
        ),
        document_date=document.document_date,
        due_date=due_date,
        currency_code=document.currency_code,
        original_amount=amount,
    )

    db.add(item)

    await db.flush()

    return item


async def get_locked_open_item_for_invoice(
    db: AsyncSession,
    *,
    company_id: int,
    trade_document_id: int,
) -> CounterpartyOpenItem:
    """
    Lock the exact AR/AP obligation belonging to one Trade Invoice.
    """
    item = (
        await db.execute(
            select(
                CounterpartyOpenItem
            )
            .where(
                CounterpartyOpenItem.company_id
                == company_id,
                CounterpartyOpenItem.trade_document_id
                == trade_document_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()

    if item is None:
        raise CounterpartyOpenItemNotFoundError(
            "Counterparty open item for "
            "Trade Invoice not found"
        )

    return item


async def cancel_counterparty_open_item_for_invoice(
    db: AsyncSession,
    *,
    document: TradeDocument,
) -> CounterpartyOpenItem:
    """
    Cancel the open AR/AP obligation of a confirmed invoice.

    v1 allows cancellation only while the obligation is fully OPEN.

    When settlement allocations are introduced:
        PARTIALLY_SETTLED / SETTLED will remain blocked until their
        allocations are reversed.
    """
    if document.kind != TradeDocumentKind.INVOICE:
        raise CounterpartyOpenItemSourceTypeError(
            "Only Trade Invoice can cancel "
            "a counterparty open item"
        )

    if (
        document.status
        != TradeDocumentStatus.CONFIRMED
    ):
        raise CounterpartyOpenItemSourceStatusError(
            "Only confirmed Trade Invoice can cancel "
            "its counterparty open item"
        )

    item = await get_locked_open_item_for_invoice(
        db,
        company_id=document.company_id,
        trade_document_id=document.id,
    )

    if (
        item.status
        != CounterpartyOpenItemStatus.OPEN
    ):
        raise CounterpartyOpenItemStateError(
            "Only open counterparty obligation "
            "can be cancelled with the invoice"
        )

    expected_type = (
        get_open_item_type_for_invoice(
            document
        )
    )

    if item.item_type != expected_type:
        raise CounterpartyOpenItemStateError(
            "Counterparty open-item type does not match "
            "Trade Invoice direction"
        )

    if (
        item.counterparty_id
        != document.counterparty_id
    ):
        raise CounterpartyOpenItemStateError(
            "Counterparty open item does not match "
            "Trade Invoice counterparty"
        )

    if (
        item.contract_id
        != document.contract_id
    ):
        raise CounterpartyOpenItemStateError(
            "Counterparty open item does not match "
            "Trade Invoice contract"
        )

    if (
        item.currency_code
        != document.currency_code
    ):
        raise CounterpartyOpenItemStateError(
            "Counterparty open item does not match "
            "Trade Invoice currency"
        )

    item.status = (
        CounterpartyOpenItemStatus.CANCELLED
    )

    await db.flush()

    return item
