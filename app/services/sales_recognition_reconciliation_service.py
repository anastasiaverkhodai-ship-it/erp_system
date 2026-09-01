from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import (
    Document,
    DocumentStatus,
    DocumentType,
)
from app.models.invoice_fulfillment_allocation import (
    InvoiceFulfillmentAllocation,
)
from app.models.sales_recognition_event import (
    SalesRecognitionEvent,
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
from app.services.invoice_fulfillment_allocation_types import (
    InvoiceFulfillmentAllocationStatus,
)
from app.services.money_rounding import (
    round_currency_amount,
)
from app.services.sales_recognition_calculation_service import (
    SalesRecognitionCandidate,
    SalesRecognitionDataIntegrityError,
    SalesRecognitionTarget,
    build_sales_recognition_targets,
    order_sales_recognition_reconciliations,
)
from app.services.sales_recognition_persistence_service import (
    build_current_sales_recognition_targets,
    reconcile_sales_recognition_source,
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


class SalesRecognitionReconciliationError(Exception):
    """Base invoice-line Sales recognition reconciliation error."""


class SalesRecognitionInvoiceLineNotFoundError(
    SalesRecognitionReconciliationError
):
    """Invoice or invoice line does not exist."""


class SalesRecognitionInvoiceLineStateError(
    SalesRecognitionReconciliationError
):
    """Invoice source state is not eligible for Sales recognition."""


@dataclass(
    frozen=True,
    slots=True,
)
class SalesRecognitionInvoiceLineSnapshot:
    """
    Immutable commercial amount basis used by recognition.

    gross_amount is always the tax-inclusive Invoice line amount.

    VAT-configured line:
        TaxCalculation.taxable_base + TaxCalculation.tax_amount

    Non-VAT line:
        TradeDocumentLine.quantity * unit_price
    """

    invoice_id: int
    invoice_line_id: int
    quantity: Decimal
    gross_amount: Decimal
    tax_amount: Decimal
    currency_code: str


@dataclass(
    frozen=True,
    slots=True,
)
class SalesRecognitionReconciliationResult:
    invoice_id: int
    invoice_line_id: int
    current_targets: tuple[
        SalesRecognitionTarget,
        ...,
    ]
    desired_targets: tuple[
        SalesRecognitionTarget,
        ...,
    ]
    adjustments: tuple[
        SalesRecognitionTarget,
        ...,
    ]
    created_events: tuple[
        SalesRecognitionEvent,
        ...,
    ]


def _decimal(
    value,
) -> Decimal:
    return Decimal(
        str(value)
    )


def build_sales_recognition_invoice_line_snapshot(
    *,
    document: TradeDocument,
    line: TradeDocumentLine,
    calculation: TaxCalculation | None,
) -> SalesRecognitionInvoiceLineSnapshot:
    """
    Build immutable commercial Sales recognition truth.

    Warehouse / Sales Order prices are deliberately absent here.
    """

    if document.id is None:
        raise SalesRecognitionDataIntegrityError(
            "Persistent Sales Invoice ID is required"
        )

    if line.id is None:
        raise SalesRecognitionDataIntegrityError(
            "Persistent Sales Invoice line ID is required"
        )

    if (
        document.direction
        != TradeDirection.SALE
    ):
        raise SalesRecognitionInvoiceLineStateError(
            "Sales recognition requires SALE invoice"
        )

    if (
        document.kind
        != TradeDocumentKind.INVOICE
    ):
        raise SalesRecognitionInvoiceLineStateError(
            "Sales recognition requires Trade Invoice"
        )

    if (
        document.status
        != TradeDocumentStatus.CONFIRMED
    ):
        raise SalesRecognitionInvoiceLineStateError(
            "Sales recognition requires confirmed "
            "Trade Invoice"
        )

    if (
        line.company_id
        != document.company_id
    ):
        raise SalesRecognitionDataIntegrityError(
            "Sales Invoice line company mismatch"
        )

    if (
        line.trade_document_id
        != document.id
    ):
        raise SalesRecognitionDataIntegrityError(
            "Sales Invoice line source mismatch"
        )

    quantity = _decimal(
        line.quantity
    )

    if quantity <= ZERO:
        raise SalesRecognitionDataIntegrityError(
            "Sales Invoice line quantity "
            "must be greater than zero"
        )

    currency_code = str(
        document.currency_code
    )

    if len(currency_code) != 3:
        raise SalesRecognitionDataIntegrityError(
            "Sales Invoice currency code "
            "must contain exactly 3 characters"
        )

    has_tax_configuration = (
        line.tax_rate_code
        is not None
    )

    if has_tax_configuration:
        if calculation is None:
            raise SalesRecognitionDataIntegrityError(
                "VAT-configured confirmed Sales Invoice "
                "line has no immutable TaxCalculation"
            )

        if (
            calculation.company_id
            != document.company_id
            or calculation.trade_document_id
            != document.id
            or calculation.trade_document_line_id
            != line.id
            or calculation.product_id
            != line.product_id
        ):
            raise SalesRecognitionDataIntegrityError(
                "TaxCalculation Sales Invoice "
                "source mismatch"
            )

        if (
            calculation.direction
            != TaxDirection.OUTPUT
        ):
            raise SalesRecognitionDataIntegrityError(
                "Sales Invoice TaxCalculation "
                "must be OUTPUT"
            )

        if (
            calculation.currency_code
            != currency_code
        ):
            raise SalesRecognitionDataIntegrityError(
                "Sales Invoice and TaxCalculation "
                "currency mismatch"
            )

        taxable_base = _decimal(
            calculation.taxable_base
        )

        tax_amount = _decimal(
            calculation.tax_amount
        )

        if taxable_base < ZERO:
            raise SalesRecognitionDataIntegrityError(
                "Sales Invoice taxable base "
                "cannot be negative"
            )

        if tax_amount < ZERO:
            raise SalesRecognitionDataIntegrityError(
                "Sales Invoice tax amount "
                "cannot be negative"
            )

        gross_amount = round_currency_amount(
            amount=(
                taxable_base
                + tax_amount
            ),
            currency_code=currency_code,
        )

    else:
        if calculation is not None:
            raise SalesRecognitionDataIntegrityError(
                "Non-VAT Sales Invoice line "
                "unexpectedly has TaxCalculation"
            )

        gross_amount = round_currency_amount(
            amount=(
                quantity
                * _decimal(
                    line.unit_price
                )
            ),
            currency_code=currency_code,
        )

        tax_amount = round_currency_amount(
            amount=ZERO,
            currency_code=currency_code,
        )

    if gross_amount <= ZERO:
        raise SalesRecognitionDataIntegrityError(
            "Sales Invoice line gross amount "
            "must be greater than zero"
        )

    if tax_amount > gross_amount:
        raise SalesRecognitionDataIntegrityError(
            "Sales Invoice line tax amount "
            "cannot exceed gross amount"
        )

    return SalesRecognitionInvoiceLineSnapshot(
        invoice_id=document.id,
        invoice_line_id=line.id,
        quantity=quantity,
        gross_amount=gross_amount,
        tax_amount=tax_amount,
        currency_code=currency_code,
    )


async def _lock_sales_invoice_line_context(
    db: AsyncSession,
    *,
    company_id: int,
    invoice_id: int,
    invoice_line_id: int,
) -> tuple[
    TradeDocument,
    TradeDocumentLine,
    TaxCalculation | None,
]:
    """
    Lock the stable Invoice line parent before reading fulfillment
    candidates. This serializes concurrent reconciliation for one
    commercial source line.
    """

    line = (
        await db.execute(
            select(
                TradeDocumentLine
            )
            .where(
                TradeDocumentLine.company_id
                == company_id,
                TradeDocumentLine.trade_document_id
                == invoice_id,
                TradeDocumentLine.id
                == invoice_line_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()

    if line is None:
        raise SalesRecognitionInvoiceLineNotFoundError(
            "Sales Invoice line not found"
        )

    document = (
        await db.execute(
            select(
                TradeDocument
            )
            .where(
                TradeDocument.company_id
                == company_id,
                TradeDocument.id
                == invoice_id,
            )
        )
    ).scalar_one_or_none()

    if document is None:
        raise SalesRecognitionInvoiceLineNotFoundError(
            "Sales Invoice not found"
        )

    calculation = (
        await db.execute(
            select(
                TaxCalculation
            )
            .where(
                TaxCalculation.company_id
                == company_id,
                TaxCalculation.trade_document_id
                == invoice_id,
                TaxCalculation.trade_document_line_id
                == invoice_line_id,
            )
        )
    ).scalar_one_or_none()

    return (
        document,
        line,
        calculation,
    )


async def _load_active_sales_recognition_candidates(
    db: AsyncSession,
    *,
    company_id: int,
    invoice_id: int,
    invoice_line_id: int,
) -> tuple[
    SalesRecognitionCandidate,
    ...,
]:
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
                    == company_id
                ),
                (
                    InvoiceFulfillmentAllocation
                    .invoice_id
                    == invoice_id
                ),
                (
                    InvoiceFulfillmentAllocation
                    .invoice_line_id
                    == invoice_line_id
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
            raise SalesRecognitionDataIntegrityError(
                "ACTIVE Sales fulfillment allocation "
                "must reference POSTED warehouse document"
            )

        if (
            document.document_type
            != DocumentType.ISSUE
        ):
            raise SalesRecognitionDataIntegrityError(
                "Sales recognition fulfillment source "
                "must reference warehouse ISSUE"
            )

        candidates.append(
            SalesRecognitionCandidate(
                source_id=allocation.id,
                event_date=(
                    document.document_date
                ),
                quantity=_decimal(
                    allocation.quantity
                ),
            )
        )

    return tuple(
        candidates
    )


async def _load_invoice_line_sales_recognition_events(
    db: AsyncSession,
    *,
    company_id: int,
    invoice_id: int,
    invoice_line_id: int,
) -> tuple[
    SalesRecognitionEvent,
    ...,
]:
    """
    Load history for both ACTIVE and REVERSED allocations.

    Reversed allocation history must remain visible so removed
    desired sources can be reconciled to zero.
    """

    return tuple(
        (
            await db.execute(
                select(
                    SalesRecognitionEvent
                )
                .join(
                    InvoiceFulfillmentAllocation,
                    and_(
                        (
                            InvoiceFulfillmentAllocation
                            .company_id
                            == (
                                SalesRecognitionEvent
                                .company_id
                            )
                        ),
                        (
                            InvoiceFulfillmentAllocation
                            .id
                            == (
                                SalesRecognitionEvent
                                .invoice_fulfillment_allocation_id
                            )
                        ),
                    ),
                )
                .where(
                    SalesRecognitionEvent.company_id
                    == company_id,
                    (
                        InvoiceFulfillmentAllocation
                        .invoice_id
                        == invoice_id
                    ),
                    (
                        InvoiceFulfillmentAllocation
                        .invoice_line_id
                        == invoice_line_id
                    ),
                )
                .order_by(
                    SalesRecognitionEvent.id
                )
            )
        )
        .scalars()
        .all()
    )


async def reconcile_sales_recognition_for_invoice_line(
    db: AsyncSession,
    *,
    company_id: int,
    invoice_id: int,
    invoice_line_id: int,
    adjustment_date: date,
    created_by: int,
) -> SalesRecognitionReconciliationResult:
    """
    Reconcile commercial Sales recognition for one confirmed
    Sales Invoice line from all currently ACTIVE fulfillment
    allocations.

    Commercial monetary truth comes only from the immutable
    confirmed Invoice / TaxCalculation snapshot.

    Warehouse DocumentLine.price and Sales Order price are never
    used for revenue recognition.
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

    (
        document,
        line,
        calculation,
    ) = await _lock_sales_invoice_line_context(
        db,
        company_id=company_id,
        invoice_id=invoice_id,
        invoice_line_id=invoice_line_id,
    )

    snapshot = (
        build_sales_recognition_invoice_line_snapshot(
            document=document,
            line=line,
            calculation=calculation,
        )
    )

    candidates = (
        await _load_active_sales_recognition_candidates(
            db,
            company_id=company_id,
            invoice_id=invoice_id,
            invoice_line_id=invoice_line_id,
        )
    )

    desired_targets = (
        build_sales_recognition_targets(
            invoice_line_quantity=(
                snapshot.quantity
            ),
            invoice_line_gross_amount=(
                snapshot.gross_amount
            ),
            invoice_line_tax_amount=(
                snapshot.tax_amount
            ),
            currency_code=(
                snapshot.currency_code
            ),
            candidates=candidates,
        )
    )

    events = (
        await _load_invoice_line_sales_recognition_events(
            db,
            company_id=company_id,
            invoice_id=invoice_id,
            invoice_line_id=invoice_line_id,
        )
    )

    current_targets = (
        build_current_sales_recognition_targets(
            events=events,
            currency_code=(
                snapshot.currency_code
            ),
        )
    )

    adjustments = (
        order_sales_recognition_reconciliations(
            current_targets=current_targets,
            desired_targets=desired_targets,
        )
    )

    created_events = []

    for target in adjustments:
        created_events.extend(
            await reconcile_sales_recognition_source(
                db,
                company_id=company_id,
                target=target,
                currency_code=(
                    snapshot.currency_code
                ),
                created_by=created_by,
                reversal_date=adjustment_date,
            )
        )

    return SalesRecognitionReconciliationResult(
        invoice_id=invoice_id,
        invoice_line_id=invoice_line_id,
        current_targets=current_targets,
        desired_targets=desired_targets,
        adjustments=adjustments,
        created_events=tuple(
            created_events
        ),
    )
