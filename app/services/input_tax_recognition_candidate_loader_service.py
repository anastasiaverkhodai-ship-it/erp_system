from decimal import Decimal

from sqlalchemy import (
    and_,
    select,
)
from sqlalchemy.ext.asyncio import (
    AsyncSession,
)

from app.models.counterparty_open_item import (
    CounterpartyOpenItem,
)
from app.models.document import (
    Document,
    DocumentStatus,
    DocumentType,
)
from app.models.invoice_fulfillment_allocation import (
    InvoiceFulfillmentAllocation,
)
from app.models.payment import (
    Payment,
)
from app.models.payment_settlement_allocation import (
    PaymentSettlementAllocation,
)
from app.models.tax_calculation import (
    TaxCalculation,
)
from app.models.trade_document import (
    TradeDocument,
)
from app.models.trade_document_line import (
    TradeDocumentLine,
)
from app.models.trade_fulfillment import (
    TradeFulfillment,
)
from app.services.counterparty_open_item_types import (
    CounterpartyOpenItemType,
)
from app.services.invoice_fulfillment_allocation_types import (
    InvoiceFulfillmentAllocationStatus,
)
from app.services.payment_types import (
    PaymentDirection,
    PaymentSettlementAllocationStatus,
    PaymentStatus,
)
from app.services.tax_recognition_orchestration_service import (
    TaxRecognitionCandidate,
    build_fulfillment_recognition_candidate,
    build_settlement_recognition_candidate,
)
from app.services.tax_recognition_types import (
    TaxRecognitionMethod,
)
from app.services.tax_types import (
    TaxDirection,
)
from app.services.trade_document_types import (
    TradeDirection,
    TradeDocumentKind,
    TradeDocumentStatus,
)


ZERO = Decimal("0")


class InputTaxRecognitionCandidateLoaderError(
    Exception
):
    """Base purchase-side INPUT VAT candidate loader error."""


class InputTaxRecognitionCandidateLoaderStateError(
    InputTaxRecognitionCandidateLoaderError
):
    """Source document/calculation is not eligible."""


class InputTaxRecognitionCandidateLoaderIntegrityError(
    InputTaxRecognitionCandidateLoaderError
):
    """Persisted purchase-side economic source is inconsistent."""


def validate_input_purchase_calculation(
    *,
    calculation,
    invoice,
) -> TaxRecognitionMethod:
    """
    Fail closed unless this is automatic INPUT VAT recognition
    for a confirmed PURCHASE Invoice.
    """

    try:
        direction = TaxDirection(
            calculation.direction
        )
    except ValueError as exc:
        raise (
            InputTaxRecognitionCandidateLoaderIntegrityError(
                "Unsupported TaxCalculation direction"
            )
        ) from exc

    if direction != TaxDirection.INPUT:
        raise (
            InputTaxRecognitionCandidateLoaderStateError(
                "Purchase INPUT candidate loader requires "
                "TaxDirection.INPUT"
            )
        )

    try:
        method = TaxRecognitionMethod(
            calculation.recognition_method
        )
    except ValueError as exc:
        raise (
            InputTaxRecognitionCandidateLoaderIntegrityError(
                "Unsupported TaxCalculation "
                "recognition method"
            )
        ) from exc

    if method == TaxRecognitionMethod.MANUAL:
        raise (
            InputTaxRecognitionCandidateLoaderStateError(
                "MANUAL INPUT VAT recognition cannot "
                "load automatic economic candidates"
            )
        )

    if (
        invoice.company_id
        != calculation.company_id
        or invoice.id
        != calculation.trade_document_id
    ):
        raise (
            InputTaxRecognitionCandidateLoaderIntegrityError(
                "Trade Invoice does not match "
                "TaxCalculation source"
            )
        )

    if (
        invoice.direction
        != TradeDirection.PURCHASE
    ):
        raise (
            InputTaxRecognitionCandidateLoaderStateError(
                "INPUT VAT economic candidates require "
                "PURCHASE Trade Invoice"
            )
        )

    if (
        invoice.kind
        != TradeDocumentKind.INVOICE
    ):
        raise (
            InputTaxRecognitionCandidateLoaderStateError(
                "INPUT VAT economic candidates require "
                "TradeDocumentKind.INVOICE"
            )
        )

    if (
        invoice.status
        != TradeDocumentStatus.CONFIRMED
    ):
        raise (
            InputTaxRecognitionCandidateLoaderStateError(
                "INPUT VAT economic candidates require "
                "CONFIRMED Purchase Invoice"
            )
        )

    if (
        invoice.currency_code
        != calculation.currency_code
    ):
        raise (
            InputTaxRecognitionCandidateLoaderIntegrityError(
                "Purchase Invoice and TaxCalculation "
                "currency mismatch"
            )
        )

    return method


def candidate_kinds_for_method(
    method: TaxRecognitionMethod,
) -> tuple[
    str,
    ...,
]:
    """
    FIRST_EVENT:
        receipt + supplier settlement compete chronologically.

    CASH_METHOD:
        only supplier settlement is economically eligible.
    """

    if (
        method
        == TaxRecognitionMethod.FIRST_EVENT
    ):
        return (
            "fulfillment",
            "settlement",
        )

    if (
        method
        == TaxRecognitionMethod.CASH_METHOD
    ):
        return (
            "settlement",
        )

    if (
        method
        == TaxRecognitionMethod.MANUAL
    ):
        raise (
            InputTaxRecognitionCandidateLoaderStateError(
                "MANUAL INPUT VAT recognition has no "
                "automatic candidate kinds"
            )
        )

    raise (
        InputTaxRecognitionCandidateLoaderIntegrityError(
            "Unsupported INPUT VAT recognition method"
        )
    )


async def _load_purchase_invoice(
    db: AsyncSession,
    *,
    calculation: TaxCalculation,
) -> TradeDocument:
    invoice = (
        await db.execute(
            select(
                TradeDocument
            )
            .where(
                TradeDocument.company_id
                == calculation.company_id,
                TradeDocument.id
                == calculation.trade_document_id,
            )
        )
    ).scalar_one_or_none()

    if invoice is None:
        raise (
            InputTaxRecognitionCandidateLoaderIntegrityError(
                "TaxCalculation Purchase Invoice "
                "does not exist"
            )
        )

    return invoice


async def _load_invoice_line_quantity(
    db: AsyncSession,
    *,
    calculation: TaxCalculation,
) -> Decimal:
    quantity = (
        await db.execute(
            select(
                TradeDocumentLine.quantity
            )
            .where(
                TradeDocumentLine.company_id
                == calculation.company_id,
                TradeDocumentLine.trade_document_id
                == calculation.trade_document_id,
                TradeDocumentLine.id
                == calculation.trade_document_line_id,
            )
        )
    ).scalar_one_or_none()

    if quantity is None:
        raise (
            InputTaxRecognitionCandidateLoaderIntegrityError(
                "TaxCalculation Purchase Invoice line "
                "does not exist"
            )
        )

    quantity = Decimal(
        quantity
    )

    if quantity <= ZERO:
        raise (
            InputTaxRecognitionCandidateLoaderIntegrityError(
                "Purchase Invoice line quantity "
                "must be positive"
            )
        )

    return quantity


async def _load_purchase_fulfillment_candidates(
    db: AsyncSession,
    *,
    calculation: TaxCalculation,
) -> tuple[
    TaxRecognitionCandidate,
    ...,
]:
    """
    Active InvoiceFulfillmentAllocation for a PURCHASE Invoice
    must point through TradeFulfillment to a POSTED warehouse
    RECEIPT.
    """

    invoice_line_quantity = (
        await _load_invoice_line_quantity(
            db,
            calculation=calculation,
        )
    )

    rows = (
        await db.execute(
            select(
                InvoiceFulfillmentAllocation,
                Document,
            )
            .join(
                TradeFulfillment,
                and_(
                    TradeFulfillment.company_id
                    == (
                        InvoiceFulfillmentAllocation
                        .company_id
                    ),
                    TradeFulfillment.id
                    == (
                        InvoiceFulfillmentAllocation
                        .fulfillment_id
                    ),
                ),
            )
            .join(
                Document,
                and_(
                    Document.company_id
                    == TradeFulfillment.company_id,
                    Document.id
                    == (
                        TradeFulfillment
                        .warehouse_document_id
                    ),
                ),
            )
            .where(
                (
                    InvoiceFulfillmentAllocation
                    .company_id
                    == calculation.company_id
                ),
                (
                    InvoiceFulfillmentAllocation
                    .invoice_id
                    == calculation.trade_document_id
                ),
                (
                    InvoiceFulfillmentAllocation
                    .invoice_line_id
                    == (
                        calculation
                        .trade_document_line_id
                    )
                ),
                (
                    InvoiceFulfillmentAllocation
                    .status
                    == (
                        InvoiceFulfillmentAllocationStatus
                        .ACTIVE
                    )
                ),
            )
            .order_by(
                Document.document_date,
                InvoiceFulfillmentAllocation.id,
            )
        )
    ).all()

    candidates = []

    for allocation, document in rows:
        if (
            document.status
            != DocumentStatus.POSTED
        ):
            raise (
                InputTaxRecognitionCandidateLoaderIntegrityError(
                    "ACTIVE Purchase fulfillment "
                    "allocation must reference a "
                    "POSTED warehouse document"
                )
            )

        if (
            document.document_type
            != DocumentType.RECEIPT
        ):
            raise (
                InputTaxRecognitionCandidateLoaderIntegrityError(
                    "INPUT VAT Purchase fulfillment "
                    "source must reference warehouse "
                    "RECEIPT"
                )
            )

        candidate = (
            build_fulfillment_recognition_candidate(
                calculation=calculation,
                source_id=allocation.id,
                event_date=(
                    document.document_date
                ),
                allocation_quantity=Decimal(
                    allocation.quantity
                ),
                invoice_line_quantity=(
                    invoice_line_quantity
                ),
            )
        )

        candidates.append(
            candidate
        )

    return tuple(
        candidates
    )


async def _load_purchase_open_item(
    db: AsyncSession,
    *,
    calculation: TaxCalculation,
) -> CounterpartyOpenItem:
    item = (
        await db.execute(
            select(
                CounterpartyOpenItem
            )
            .where(
                CounterpartyOpenItem.company_id
                == calculation.company_id,
                CounterpartyOpenItem.trade_document_id
                == calculation.trade_document_id,
            )
        )
    ).scalar_one_or_none()

    if item is None:
        raise (
            InputTaxRecognitionCandidateLoaderIntegrityError(
                "Purchase Invoice has no "
                "CounterpartyOpenItem"
            )
        )

    if (
        item.item_type
        != CounterpartyOpenItemType.PAYABLE
    ):
        raise (
            InputTaxRecognitionCandidateLoaderIntegrityError(
                "INPUT VAT Purchase settlement "
                "requires PAYABLE open item"
            )
        )

    if (
        item.currency_code
        != calculation.currency_code
    ):
        raise (
            InputTaxRecognitionCandidateLoaderIntegrityError(
                "TaxCalculation and Purchase open "
                "item currency mismatch"
            )
        )

    if (
        Decimal(
            item.original_amount
        )
        <= ZERO
    ):
        raise (
            InputTaxRecognitionCandidateLoaderIntegrityError(
                "Purchase Invoice open item amount "
                "must be positive"
            )
        )

    return item


async def _load_purchase_settlement_candidates(
    db: AsyncSession,
    *,
    calculation: TaxCalculation,
) -> tuple[
    TaxRecognitionCandidate,
    ...,
]:
    open_item = (
        await _load_purchase_open_item(
            db,
            calculation=calculation,
        )
    )

    rows = (
        await db.execute(
            select(
                PaymentSettlementAllocation,
                Payment,
            )
            .join(
                Payment,
                and_(
                    Payment.company_id
                    == (
                        PaymentSettlementAllocation
                        .company_id
                    ),
                    Payment.id
                    == (
                        PaymentSettlementAllocation
                        .payment_id
                    ),
                ),
            )
            .where(
                (
                    PaymentSettlementAllocation
                    .company_id
                    == calculation.company_id
                ),
                (
                    PaymentSettlementAllocation
                    .open_item_id
                    == open_item.id
                ),
                (
                    PaymentSettlementAllocation
                    .status
                    == (
                        PaymentSettlementAllocationStatus
                        .ACTIVE
                    )
                ),
            )
            .order_by(
                Payment.payment_date,
                PaymentSettlementAllocation.id,
            )
        )
    ).all()

    candidates = []

    for allocation, payment in rows:
        if (
            payment.status
            != PaymentStatus.CONFIRMED
        ):
            raise (
                InputTaxRecognitionCandidateLoaderIntegrityError(
                    "ACTIVE Purchase settlement "
                    "allocation must reference "
                    "CONFIRMED payment"
                )
            )

        if (
            payment.direction
            != PaymentDirection.OUTGOING
        ):
            raise (
                InputTaxRecognitionCandidateLoaderIntegrityError(
                    "INPUT VAT Purchase settlement "
                    "source must use OUTGOING payment"
                )
            )

        if (
            payment.currency_code
            != calculation.currency_code
        ):
            raise (
                InputTaxRecognitionCandidateLoaderIntegrityError(
                    "TaxCalculation and supplier "
                    "payment currency mismatch"
                )
            )

        candidate = (
            build_settlement_recognition_candidate(
                calculation=calculation,
                source_id=allocation.id,
                event_date=(
                    payment.payment_date
                ),
                settlement_amount=Decimal(
                    allocation.amount
                ),
                invoice_total_amount=Decimal(
                    open_item.original_amount
                ),
            )
        )

        candidates.append(
            candidate
        )

    return tuple(
        candidates
    )


async def load_active_input_tax_recognition_candidates(
    db: AsyncSession,
    *,
    calculation: TaxCalculation,
) -> tuple[
    TaxRecognitionCandidate,
    ...,
]:
    """
    Load purchase-side economic candidates for automatic
    INPUT VAT recognition.

    FIRST_EVENT:
        POSTED warehouse RECEIPT allocations
        + CONFIRMED OUTGOING supplier settlements.

    CASH_METHOD:
        CONFIRMED OUTGOING supplier settlements only.

    Evidence is deliberately NOT loaded here.
    TaxCreditEvidence is a separate legal eligibility gate.
    """

    invoice = await _load_purchase_invoice(
        db,
        calculation=calculation,
    )

    method = validate_input_purchase_calculation(
        calculation=calculation,
        invoice=invoice,
    )

    kinds = candidate_kinds_for_method(
        method
    )

    candidates = []

    if "fulfillment" in kinds:
        candidates.extend(
            await _load_purchase_fulfillment_candidates(
                db,
                calculation=calculation,
            )
        )

    if "settlement" in kinds:
        candidates.extend(
            await _load_purchase_settlement_candidates(
                db,
                calculation=calculation,
            )
        )

    return tuple(
        candidates
    )
