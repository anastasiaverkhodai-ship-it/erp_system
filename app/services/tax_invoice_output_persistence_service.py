from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Literal

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models.company import Company
from app.models.company_vat_policy import CompanyVatPolicy
from app.models.counterparty import Counterparty
from app.models.counterparty_vat_registration import (
    CounterpartyVatRegistration,
)
from app.models.invoice_fulfillment_allocation import (
    InvoiceFulfillmentAllocation,
)
from app.models.order_vat_advance import OrderVatAdvance
from app.models.payment_settlement_allocation import (
    PaymentSettlementAllocation,
)
from app.models.product import Product
from app.models.product_tax_classification import (
    ProductTaxClassification,
)
from app.models.tax_calculation import TaxCalculation
from app.models.tax_invoice import TaxInvoice
from app.models.tax_invoice_line import TaxInvoiceLine
from app.models.tax_recognition_event import TaxRecognitionEvent
from app.models.trade_document import TradeDocument
from app.models.trade_document_line import TradeDocumentLine
from app.models.trade_fulfillment import TradeFulfillment
from app.services.accounting_period_service import ensure_period_open


OutputTaxInvoiceSourceKind = Literal[
    "fulfillment",
    "settlement",
    "order_advance",
]


class TaxInvoiceOutputError(Exception):
    """Base OUTPUT tax-invoice persistence error."""


class TaxInvoiceOutputSourceError(TaxInvoiceOutputError):
    """Source identity or source provenance is invalid."""


class TaxInvoiceOutputSnapshotError(TaxInvoiceOutputError):
    """Required immutable statutory snapshot cannot be produced."""


class TaxInvoiceOutputIdempotencyError(TaxInvoiceOutputError):
    """The same immutable source is reused with conflicting input."""


@dataclass(frozen=True)
class OutputTaxInvoiceLineAmounts:
    quantity: Decimal
    unit_price_without_vat: Decimal
    taxable_base: Decimal
    tax_amount: Decimal
    total_with_vat: Decimal


_QTY = Decimal("0.000001")
_PRICE = Decimal("0.000001")


def _enum_text(value: object) -> str:
    raw = getattr(value, "value", value)
    return str(raw).strip().lower()


def _positive_id(value: int, *, field: str) -> int:
    if not isinstance(value, int) or value <= 0:
        raise TaxInvoiceOutputSourceError(
            f"{field} must be a positive integer"
        )
    return value


def _document_number(value: str) -> str:
    if not isinstance(value, str):
        raise TaxInvoiceOutputSourceError(
            "document_number must be a string"
        )

    normalized = value.strip()

    if not normalized or len(normalized) > 120:
        raise TaxInvoiceOutputSourceError(
            "document_number must contain 1 to 120 characters"
        )

    return normalized


def _required_text(
    value: object,
    *,
    field: str,
) -> str:
    if value is None:
        raise TaxInvoiceOutputSnapshotError(
            f"{field} is required"
        )

    normalized = str(value).strip()

    if not normalized:
        raise TaxInvoiceOutputSnapshotError(
            f"{field} is required"
        )

    return normalized


def build_output_tax_invoice_line_amounts(
    *,
    source_line_quantity: Decimal,
    calculation_taxable_base: Decimal,
    recognized_taxable_base: Decimal,
    recognized_tax_amount: Decimal,
) -> OutputTaxInvoiceLineAmounts:
    """
    Build the immutable line slice represented by one first-event
    recognition increment.

    V1 intentionally supports ordinary positive taxable-base OUTPUT
    recognition. Zero-base/exempt/special statutory cases fail closed
    until their explicit legal-document rules are implemented.
    """

    quantity = Decimal(source_line_quantity)
    calculation_base = Decimal(
        calculation_taxable_base
    )
    recognized_base = Decimal(
        recognized_taxable_base
    )
    tax = Decimal(recognized_tax_amount)

    if quantity <= 0:
        raise TaxInvoiceOutputSnapshotError(
            "source line quantity must be positive"
        )

    if calculation_base <= 0:
        raise TaxInvoiceOutputSnapshotError(
            "10.6 V1 requires a positive calculation taxable base"
        )

    if recognized_base <= 0:
        raise TaxInvoiceOutputSnapshotError(
            "recognized taxable base must be positive"
        )

    if recognized_base > calculation_base:
        raise TaxInvoiceOutputSnapshotError(
            "recognized taxable base exceeds calculation taxable base"
        )

    if tax < 0:
        raise TaxInvoiceOutputSnapshotError(
            "recognized tax amount cannot be negative"
        )

    ratio = recognized_base / calculation_base

    recognized_quantity = (
        quantity * ratio
    ).quantize(
        _QTY,
        rounding=ROUND_HALF_UP,
    )

    if recognized_quantity <= 0:
        raise TaxInvoiceOutputSnapshotError(
            "recognized quantity rounds to zero"
        )

    unit_price = (
        recognized_base / recognized_quantity
    ).quantize(
        _PRICE,
        rounding=ROUND_HALF_UP,
    )

    return OutputTaxInvoiceLineAmounts(
        quantity=recognized_quantity,
        unit_price_without_vat=unit_price,
        taxable_base=recognized_base,
        tax_amount=tax,
        total_with_vat=recognized_base + tax,
    )


async def _lock_source(
    db: AsyncSession,
    *,
    company_id: int,
    source_kind: OutputTaxInvoiceSourceKind,
    source_id: int,
) -> None:
    source_id = _positive_id(
        source_id,
        field="source_id",
    )

    if source_kind == "fulfillment":
        source = await db.scalar(
            select(TradeFulfillment)
            .where(
                TradeFulfillment.id == source_id,
                TradeFulfillment.company_id == company_id,
            )
            .with_for_update()
        )

    elif source_kind == "settlement":
        source = await db.scalar(
            select(PaymentSettlementAllocation)
            .where(
                PaymentSettlementAllocation.id == source_id,
                PaymentSettlementAllocation.company_id
                == company_id,
            )
            .with_for_update()
        )

    elif source_kind == "order_advance":
        source = await db.scalar(
            select(OrderVatAdvance)
            .where(
                OrderVatAdvance.id == source_id,
                OrderVatAdvance.company_id == company_id,
            )
            .with_for_update()
        )

    else:
        raise TaxInvoiceOutputSourceError(
            f"unsupported OUTPUT source_kind={source_kind!r}"
        )

    if source is None:
        raise TaxInvoiceOutputSourceError(
            "OUTPUT tax-invoice source does not belong "
            "to the company or does not exist"
        )


def _source_filter(
    *,
    company_id: int,
    source_kind: OutputTaxInvoiceSourceKind,
    source_id: int,
):
    if source_kind == "fulfillment":
        return (
            TaxInvoice.company_id == company_id,
            TaxInvoice.source_fulfillment_id == source_id,
        )

    if source_kind == "settlement":
        return (
            TaxInvoice.company_id == company_id,
            TaxInvoice.source_payment_settlement_allocation_id
            == source_id,
        )

    if source_kind == "order_advance":
        return (
            TaxInvoice.company_id == company_id,
            TaxInvoice.source_order_vat_advance_id == source_id,
        )

    raise TaxInvoiceOutputSourceError(
        f"unsupported OUTPUT source_kind={source_kind!r}"
    )


async def _load_original_events(
    db: AsyncSession,
    *,
    company_id: int,
    source_kind: OutputTaxInvoiceSourceKind,
    source_id: int,
) -> list[TaxRecognitionEvent]:
    reversal = aliased(
        TaxRecognitionEvent
    )

    active_original = ~select(
        reversal.id
    ).where(
        reversal.company_id
        == TaxRecognitionEvent.company_id,
        reversal.tax_calculation_id
        == TaxRecognitionEvent.tax_calculation_id,
        reversal.reversal_of_id
        == TaxRecognitionEvent.id,
    ).exists()

    if source_kind == "fulfillment":
        statement = (
            select(TaxRecognitionEvent)
            .join(
                InvoiceFulfillmentAllocation,
                and_(
                    InvoiceFulfillmentAllocation.company_id
                    == TaxRecognitionEvent.company_id,
                    InvoiceFulfillmentAllocation.id
                    == TaxRecognitionEvent
                    .invoice_fulfillment_allocation_id,
                ),
            )
            .where(
                TaxRecognitionEvent.company_id == company_id,
                InvoiceFulfillmentAllocation.company_id
                == company_id,
                InvoiceFulfillmentAllocation.fulfillment_id
                == source_id,
                TaxRecognitionEvent.reversal_of_id.is_(None),
                active_original,
            )
            .order_by(TaxRecognitionEvent.id)
        )

    elif source_kind == "settlement":
        statement = (
            select(TaxRecognitionEvent)
            .where(
                TaxRecognitionEvent.company_id == company_id,
                TaxRecognitionEvent
                .payment_settlement_allocation_id
                == source_id,
                TaxRecognitionEvent.reversal_of_id.is_(None),
                active_original,
            )
            .order_by(TaxRecognitionEvent.id)
        )

    elif source_kind == "order_advance":
        statement = (
            select(TaxRecognitionEvent)
            .where(
                TaxRecognitionEvent.company_id == company_id,
                TaxRecognitionEvent.order_vat_advance_id
                == source_id,
                TaxRecognitionEvent.reversal_of_id.is_(None),
                active_original,
            )
            .order_by(TaxRecognitionEvent.id)
        )

    else:
        raise TaxInvoiceOutputSourceError(
            f"unsupported OUTPUT source_kind={source_kind!r}"
        )

    return list(
        (
            await db.scalars(statement)
        ).all()
    )


async def _load_classification(
    db: AsyncSession,
    *,
    company_id: int,
    product_id: int,
    document_date,
) -> ProductTaxClassification:
    result = await db.scalar(
        select(ProductTaxClassification)
        .where(
            ProductTaxClassification.company_id
            == company_id,
            ProductTaxClassification.product_id
            == product_id,
            ProductTaxClassification.effective_from
            <= document_date,
        )
        .order_by(
            ProductTaxClassification.effective_from.desc(),
            ProductTaxClassification.id.desc(),
        )
        .limit(1)
    )

    if result is None:
        raise TaxInvoiceOutputSnapshotError(
            "product has no statutory classification "
            "effective on tax-invoice date"
        )

    return result


async def _load_party_snapshot(
    db: AsyncSession,
    *,
    company_id: int,
    trade_document: TradeDocument,
) -> dict[str, str]:
    if trade_document.company_id != company_id:
        raise TaxInvoiceOutputSourceError(
            "trade document company mismatch"
        )

    if trade_document.vat_policy_id is None:
        raise TaxInvoiceOutputSnapshotError(
            "trade document has no company VAT policy snapshot"
        )

    if (
        trade_document.counterparty_vat_registration_id
        is None
    ):
        raise TaxInvoiceOutputSnapshotError(
            "trade document has no counterparty VAT registration snapshot"
        )

    company = await db.scalar(
        select(Company).where(
            Company.id == company_id
        )
    )

    counterparty = await db.scalar(
        select(Counterparty).where(
            Counterparty.id
            == trade_document.counterparty_id,
            Counterparty.company_id == company_id,
        )
    )

    policy = await db.scalar(
        select(CompanyVatPolicy).where(
            CompanyVatPolicy.id
            == trade_document.vat_policy_id,
            CompanyVatPolicy.company_id
            == company_id,
        )
    )

    registration = await db.scalar(
        select(CounterpartyVatRegistration).where(
            CounterpartyVatRegistration.id
            == trade_document
            .counterparty_vat_registration_id,
            CounterpartyVatRegistration.company_id
            == company_id,
            CounterpartyVatRegistration.counterparty_id
            == trade_document.counterparty_id,
        )
    )

    if (
        company is None
        or counterparty is None
        or policy is None
        or registration is None
    ):
        raise TaxInvoiceOutputSnapshotError(
            "VAT party snapshot provenance is incomplete"
        )

    seller_tax = (
        getattr(company, "edrpou", None)
        or getattr(company, "vat_number", None)
    )

    buyer_tax = (
        getattr(counterparty, "tax_number", None)
        or getattr(counterparty, "edrpou", None)
    )

    return {
        "seller_name": _required_text(
            company.name,
            field="seller_name",
        ),
        "seller_tax_number": _required_text(
            seller_tax,
            field="seller_tax_number",
        ),
        "seller_vat_number": _required_text(
            policy.vat_number,
            field="seller_vat_number",
        ),
        "buyer_name": _required_text(
            counterparty.name,
            field="buyer_name",
        ),
        "buyer_tax_number": _required_text(
            buyer_tax,
            field="buyer_tax_number",
        ),
        "buyer_vat_number": _required_text(
            registration.vat_number,
            field="buyer_vat_number",
        ),
    }


async def create_output_tax_invoice(
    db: AsyncSession,
    *,
    company_id: int,
    source_kind: OutputTaxInvoiceSourceKind,
    source_id: int,
    document_number: str,
    created_by: int,
) -> TaxInvoice:
    """
    Create one canonical OUTPUT tax invoice from one immutable economic
    first-event source.

    Natural idempotency:
      one OUTPUT TaxInvoice per fulfillment / settlement allocation /
      order VAT advance.

    Caller owns commit/rollback.
    """

    company_id = _positive_id(
        company_id,
        field="company_id",
    )
    source_id = _positive_id(
        source_id,
        field="source_id",
    )
    created_by = _positive_id(
        created_by,
        field="created_by",
    )
    number = _document_number(
        document_number
    )

    await _lock_source(
        db,
        company_id=company_id,
        source_kind=source_kind,
        source_id=source_id,
    )

    existing = await db.scalar(
        select(TaxInvoice)
        .where(
            *_source_filter(
                company_id=company_id,
                source_kind=source_kind,
                source_id=source_id,
            )
        )
        .with_for_update()
    )

    if existing is not None:
        if (
            existing.direction != "output"
            or existing.source_kind != source_kind
            or existing.document_number != number
        ):
            raise TaxInvoiceOutputIdempotencyError(
                "immutable OUTPUT source already belongs "
                "to a conflicting tax invoice"
            )

        return existing

    events = await _load_original_events(
        db,
        company_id=company_id,
        source_kind=source_kind,
        source_id=source_id,
    )

    if not events:
        raise TaxInvoiceOutputSourceError(
            "OUTPUT source has no original VAT recognition events"
        )

    event_dates = {
        event.recognition_date
        for event in events
    }

    currencies = {
        str(event.currency_code).upper()
        for event in events
    }

    if len(event_dates) != 1:
        raise TaxInvoiceOutputSourceError(
            "OUTPUT source spans multiple recognition dates"
        )

    if currencies != {"UAH"}:
        raise TaxInvoiceOutputSourceError(
            "10.6 V1 supports OUTPUT tax invoices in UAH only"
        )

    document_date = next(
        iter(event_dates)
    )

    await ensure_period_open(
        db=db,
        company_id=company_id,
        operation_date=document_date,
    )

    calculation_ids = [
        event.tax_calculation_id
        for event in events
    ]

    if len(set(calculation_ids)) != len(
        calculation_ids
    ):
        raise TaxInvoiceOutputSourceError(
            "OUTPUT source contains more than one "
            "original recognition event for a tax calculation"
        )

    calculations = {
        item.id: item
        for item in (
            await db.scalars(
                select(TaxCalculation).where(
                    TaxCalculation.company_id
                    == company_id,
                    TaxCalculation.id.in_(
                        calculation_ids
                    ),
                )
            )
        ).all()
    }

    if set(calculations) != set(
        calculation_ids
    ):
        raise TaxInvoiceOutputSourceError(
            "tax-calculation provenance is incomplete"
        )

    trade_document_ids = {
        calculation.trade_document_id
        for calculation in calculations.values()
    }

    if len(trade_document_ids) != 1:
        raise TaxInvoiceOutputSourceError(
            "one OUTPUT tax invoice source must resolve "
            "to exactly one trade document"
        )

    trade_document_id = next(
        iter(trade_document_ids)
    )

    trade_document = await db.scalar(
        select(TradeDocument).where(
            TradeDocument.id
            == trade_document_id,
            TradeDocument.company_id
            == company_id,
        )
    )

    if trade_document is None:
        raise TaxInvoiceOutputSourceError(
            "trade document provenance is missing"
        )

    party = await _load_party_snapshot(
        db,
        company_id=company_id,
        trade_document=trade_document,
    )

    header_kwargs = {
        "company_id": company_id,
        "direction": "output",
        "document_number": number,
        "document_date": document_date,
        "currency_code": "UAH",
        **party,
        "source_kind": source_kind,
        "source_fulfillment_id": (
            source_id
            if source_kind == "fulfillment"
            else None
        ),
        "source_payment_settlement_allocation_id": (
            source_id
            if source_kind == "settlement"
            else None
        ),
        "source_order_vat_advance_id": (
            source_id
            if source_kind == "order_advance"
            else None
        ),
        "created_by": created_by,
    }

    tax_invoice = TaxInvoice(
        **header_kwargs
    )

    db.add(tax_invoice)
    await db.flush()

    for line_number, event in enumerate(
        events,
        start=1,
    ):
        calculation = calculations[
            event.tax_calculation_id
        ]

        if _enum_text(
            calculation.direction
        ) != "output":
            raise TaxInvoiceOutputSourceError(
                "OUTPUT tax invoice cannot use INPUT tax calculation"
            )

        source_line = await db.scalar(
            select(TradeDocumentLine).where(
                TradeDocumentLine.id
                == calculation.trade_document_line_id,
                TradeDocumentLine.company_id
                == company_id,
                TradeDocumentLine.trade_document_id
                == trade_document_id,
                TradeDocumentLine.product_id
                == calculation.product_id,
            )
        )

        if source_line is None:
            raise TaxInvoiceOutputSourceError(
                "trade-document line provenance is missing"
            )

        product = await db.scalar(
            select(Product).where(
                Product.id == calculation.product_id,
                Product.company_id == company_id,
            )
        )

        if product is None:
            raise TaxInvoiceOutputSnapshotError(
                "product provenance is missing"
            )

        uom_code = _required_text(
            product.base_uom_code,
            field="product.base_uom_code",
        )

        classification = await _load_classification(
            db,
            company_id=company_id,
            product_id=calculation.product_id,
            document_date=document_date,
        )

        amounts = build_output_tax_invoice_line_amounts(
            source_line_quantity=source_line.quantity,
            calculation_taxable_base=(
                calculation.taxable_base
            ),
            recognized_taxable_base=(
                event.recognized_taxable_base
            ),
            recognized_tax_amount=(
                event.recognized_tax_amount
            ),
        )

        db.add(
            TaxInvoiceLine(
                company_id=company_id,
                tax_invoice_id=tax_invoice.id,
                line_number=line_number,
                tax_calculation_id=calculation.id,
                tax_recognition_event_id=event.id,
                product_id=calculation.product_id,
                description=_required_text(
                    product.name,
                    field="product.name",
                ),
                quantity=amounts.quantity,
                uom_code=uom_code,
                classification_kind=(
                    classification
                    .classification_kind
                ),
                statutory_code=(
                    classification
                    .statutory_code
                ),
                unit_price_without_vat=(
                    amounts
                    .unit_price_without_vat
                ),
                taxable_base=(
                    amounts.taxable_base
                ),
                tax_rate_code=_required_text(
                    calculation.tax_rate_code,
                    field="tax_rate_code",
                ),
                tax_rate=calculation.tax_rate,
                tax_amount=amounts.tax_amount,
                total_with_vat=(
                    amounts.total_with_vat
                ),
            )
        )

    await db.flush()

    return tax_invoice
