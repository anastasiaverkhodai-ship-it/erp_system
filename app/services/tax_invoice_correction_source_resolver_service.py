from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Literal

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models.purchase_return_recognition_event import (
    PurchaseReturnRecognitionEvent,
)
from app.models.purchase_return_vat_adjustment_event import (
    PurchaseReturnVatAdjustmentEvent,
)
from app.models.purchase_value_correction_vat_adjustment_event import (
    PurchaseValueCorrectionVatAdjustmentEvent,
)
from app.models.sales_recognition_event import (
    SalesRecognitionEvent,
)
from app.models.sales_return_recognition_event import (
    SalesReturnRecognitionEvent,
)
from app.models.tax_calculation import TaxCalculation
from app.models.tax_credit_evidence import TaxCreditEvidence
from app.models.tax_invoice import TaxInvoice
from app.models.tax_invoice_line import TaxInvoiceLine
from app.models.tax_recognition_event import TaxRecognitionEvent
from app.models.trade_value_correction_event import (
    TradeValueCorrectionEvent,
)


TaxInvoiceCorrectionSourceKind = Literal[
    "sales_return",
    "sales_value_correction",
    "purchase_return",
    "purchase_value_correction",
    "recognition_reversal",
]


OUTPUT_SOURCE_KINDS = frozenset(
    {
        "sales_return",
        "sales_value_correction",
        "recognition_reversal",
    }
)

INPUT_SOURCE_KINDS = frozenset(
    {
        "purchase_return",
        "purchase_value_correction",
    }
)


class TaxInvoiceCorrectionSourceError(Exception):
    """Canonical RK economic-source validation failed."""


class TaxInvoiceCorrectionSourceMappingError(
    TaxInvoiceCorrectionSourceError
):
    """Economic source cannot map uniquely to the original PN line."""


class TaxInvoiceCorrectionEvidenceError(
    TaxInvoiceCorrectionSourceError
):
    """Secondary legal evidence is missing or inconsistent."""


@dataclass(frozen=True)
class TaxInvoiceCorrectionLineSource:
    line_number: int
    source_kind: TaxInvoiceCorrectionSourceKind
    source_id: int
    reason_code: str
    tax_credit_evidence_id: int | None = None


@dataclass(frozen=True)
class TaxInvoiceCorrectionLineAmounts:
    quantity_delta: Decimal
    unit_price_without_vat_delta: Decimal
    taxable_base_delta: Decimal
    tax_amount_delta: Decimal
    total_with_vat_delta: Decimal


@dataclass(frozen=True)
class ResolvedTaxInvoiceCorrectionLine:
    line_number: int
    original_tax_invoice_line_id: int
    source_kind: TaxInvoiceCorrectionSourceKind
    source_id: int
    sales_return_recognition_event_id: int | None
    trade_value_correction_event_id: int | None
    purchase_return_vat_adjustment_event_id: int | None
    purchase_value_correction_vat_adjustment_event_id: int | None
    tax_recognition_reversal_event_id: int | None
    tax_credit_evidence_id: int | None
    reason_code: str
    description: str
    uom_code: str
    classification_kind: str
    statutory_code: str
    tax_rate_code: str
    tax_rate: Decimal
    quantity_delta: Decimal
    unit_price_without_vat_delta: Decimal
    taxable_base_delta: Decimal
    tax_amount_delta: Decimal
    total_with_vat_delta: Decimal


def _decimal(value: object) -> Decimal:
    result = Decimal(str(value))

    if not result.is_finite():
        raise TaxInvoiceCorrectionSourceError(
            "RK amount must be finite"
        )

    return result


def _q6(value: Decimal) -> Decimal:
    return value.quantize(
        Decimal("0.000001"),
        rounding=ROUND_HALF_UP,
    )


def _positive_int(
    value: int,
    *,
    field: str,
) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value <= 0
    ):
        raise TaxInvoiceCorrectionSourceError(
            f"{field} must be a positive integer"
        )

    return value


def _required_text(
    value: object,
    *,
    field: str,
    max_length: int,
) -> str:
    if value is None:
        raise TaxInvoiceCorrectionSourceError(
            f"{field} is required"
        )

    normalized = str(value).strip()

    if not normalized:
        raise TaxInvoiceCorrectionSourceError(
            f"{field} is required"
        )

    if len(normalized) > max_length:
        raise TaxInvoiceCorrectionSourceError(
            f"{field} exceeds maximum length"
        )

    return normalized


def validate_correction_source_kind_for_direction(
    *,
    direction: str,
    source_kind: str,
) -> None:
    if direction == "output":
        if source_kind not in OUTPUT_SOURCE_KINDS:
            raise TaxInvoiceCorrectionSourceError(
                "OUTPUT RK source_kind is unsupported"
            )
        return

    if direction == "input":
        if source_kind not in INPUT_SOURCE_KINDS:
            raise TaxInvoiceCorrectionSourceError(
                "INPUT RK source_kind is unsupported"
            )
        return

    raise TaxInvoiceCorrectionSourceError(
        "unsupported tax-invoice direction"
    )


def normalize_correction_line_sources(
    sources: list[TaxInvoiceCorrectionLineSource],
) -> tuple[TaxInvoiceCorrectionLineSource, ...]:
    if not sources:
        raise TaxInvoiceCorrectionSourceError(
            "at least one RK source line is required"
        )

    normalized: list[
        TaxInvoiceCorrectionLineSource
    ] = []

    for source in sources:
        line_number = _positive_int(
            source.line_number,
            field="line_number",
        )

        source_id = _positive_int(
            source.source_id,
            field="source_id",
        )

        reason_code = _required_text(
            source.reason_code,
            field="reason_code",
            max_length=40,
        )

        evidence_id = source.tax_credit_evidence_id

        if evidence_id is not None:
            evidence_id = _positive_int(
                evidence_id,
                field="tax_credit_evidence_id",
            )

        normalized.append(
            TaxInvoiceCorrectionLineSource(
                line_number=line_number,
                source_kind=source.source_kind,
                source_id=source_id,
                reason_code=reason_code,
                tax_credit_evidence_id=evidence_id,
            )
        )

    line_numbers = {
        item.line_number
        for item in normalized
    }

    source_keys = {
        (
            item.source_kind,
            item.source_id,
        )
        for item in normalized
    }

    if len(line_numbers) != len(normalized):
        raise TaxInvoiceCorrectionSourceError(
            "RK line_number values must be unique"
        )

    if len(source_keys) != len(normalized):
        raise TaxInvoiceCorrectionSourceError(
            "RK economic sources must be unique"
        )

    return tuple(
        sorted(
            normalized,
            key=lambda item: item.line_number,
        )
    )


def _return_deltas(
    *,
    returned_quantity: object,
    returned_gross_amount: object,
    returned_tax_amount: object,
) -> TaxInvoiceCorrectionLineAmounts:
    quantity = abs(
        _decimal(returned_quantity)
    )

    gross = abs(
        _decimal(returned_gross_amount)
    )

    tax = abs(
        _decimal(returned_tax_amount)
    )

    if quantity <= 0 or gross <= 0:
        raise TaxInvoiceCorrectionSourceError(
            "return source must have positive quantity and gross"
        )

    if tax > gross:
        raise TaxInvoiceCorrectionSourceError(
            "return tax cannot exceed gross"
        )

    total_delta = -gross
    tax_delta = -tax
    base_delta = (
        total_delta
        - tax_delta
    )

    return TaxInvoiceCorrectionLineAmounts(
        quantity_delta=-quantity,
        unit_price_without_vat_delta=Decimal("0"),
        taxable_base_delta=base_delta,
        tax_amount_delta=tax_delta,
        total_with_vat_delta=total_delta,
    )


def _value_deltas(
    *,
    original_gross_amount: object,
    original_tax_amount: object,
    corrected_gross_amount: object,
    corrected_tax_amount: object,
    original_quantity: object,
) -> TaxInvoiceCorrectionLineAmounts:
    original_gross = _decimal(
        original_gross_amount
    )
    original_tax = _decimal(
        original_tax_amount
    )
    corrected_gross = _decimal(
        corrected_gross_amount
    )
    corrected_tax = _decimal(
        corrected_tax_amount
    )
    quantity = _decimal(
        original_quantity
    )

    if (
        original_gross < 0
        or original_tax < 0
        or corrected_gross < 0
        or corrected_tax < 0
    ):
        raise TaxInvoiceCorrectionSourceError(
            "value-correction source amounts must be nonnegative"
        )

    if original_tax > original_gross:
        raise TaxInvoiceCorrectionSourceError(
            "original tax cannot exceed original gross"
        )

    if corrected_tax > corrected_gross:
        raise TaxInvoiceCorrectionSourceError(
            "corrected tax cannot exceed corrected gross"
        )

    if quantity <= 0:
        raise TaxInvoiceCorrectionSourceError(
            "original PN line quantity must be positive"
        )

    total_delta = (
        corrected_gross
        - original_gross
    )

    tax_delta = (
        corrected_tax
        - original_tax
    )

    base_delta = (
        total_delta
        - tax_delta
    )

    if (
        total_delta == 0
        and tax_delta == 0
    ):
        raise TaxInvoiceCorrectionSourceError(
            "value correction must not be a no-op"
        )

    return TaxInvoiceCorrectionLineAmounts(
        quantity_delta=Decimal("0"),
        unit_price_without_vat_delta=_q6(
            base_delta / quantity
        ),
        taxable_base_delta=base_delta,
        tax_amount_delta=tax_delta,
        total_with_vat_delta=total_delta,
    )


def _purchase_adjustment_deltas(
    *,
    adjustment_kind: str,
    adjusted_taxable_base: object,
    adjusted_tax_amount: object,
    original_quantity: object,
) -> TaxInvoiceCorrectionLineAmounts:
    base = abs(
        _decimal(
            adjusted_taxable_base
        )
    )

    tax = abs(
        _decimal(
            adjusted_tax_amount
        )
    )

    quantity = _decimal(
        original_quantity
    )

    normalized_kind = str(
        adjustment_kind
    ).strip().lower()

    if normalized_kind == "increase":
        sign = Decimal("1")
    elif normalized_kind == "decrease":
        sign = Decimal("-1")
    else:
        raise TaxInvoiceCorrectionSourceError(
            "purchase value adjustment_kind is unsupported"
        )

    if (
        base == 0
        and tax == 0
    ):
        raise TaxInvoiceCorrectionSourceError(
            "purchase value adjustment must not be zero"
        )

    if quantity <= 0:
        raise TaxInvoiceCorrectionSourceError(
            "original PN line quantity must be positive"
        )

    base_delta = (
        sign * base
    )

    tax_delta = (
        sign * tax
    )

    total_delta = (
        base_delta
        + tax_delta
    )

    return TaxInvoiceCorrectionLineAmounts(
        quantity_delta=Decimal("0"),
        unit_price_without_vat_delta=_q6(
            base_delta / quantity
        ),
        taxable_base_delta=base_delta,
        tax_amount_delta=tax_delta,
        total_with_vat_delta=total_delta,
    )


def _recognition_reversal_deltas(
    *,
    original_line_quantity: object,
    recognized_taxable_base: object,
    recognized_tax_amount: object,
) -> TaxInvoiceCorrectionLineAmounts:
    quantity = abs(
        _decimal(
            original_line_quantity
        )
    )

    base = abs(
        _decimal(
            recognized_taxable_base
        )
    )

    tax = abs(
        _decimal(
            recognized_tax_amount
        )
    )

    if quantity <= 0:
        raise TaxInvoiceCorrectionSourceError(
            "original PN line quantity must be positive"
        )

    if (
        base == 0
        and tax == 0
    ):
        raise TaxInvoiceCorrectionSourceError(
            "recognition reversal must have a VAT amount"
        )

    base_delta = -base
    tax_delta = -tax

    return TaxInvoiceCorrectionLineAmounts(
        quantity_delta=-quantity,
        unit_price_without_vat_delta=Decimal("0"),
        taxable_base_delta=base_delta,
        tax_amount_delta=tax_delta,
        total_with_vat_delta=(
            base_delta
            + tax_delta
        ),
    )


async def _load_active_original(
    db: AsyncSession,
    *,
    model,
    company_id: int,
    source_id: int,
    label: str,
):
    reversal = aliased(
        model
    )

    row = await db.scalar(
        select(model)
        .where(
            model.company_id
            == company_id,
            model.id
            == source_id,
            model.reversal_of_id.is_(
                None
            ),
            ~select(
                reversal.id
            )
            .where(
                reversal.company_id
                == model.company_id,
                reversal.reversal_of_id
                == model.id,
            )
            .exists(),
        )
        .with_for_update()
    )

    if row is None:
        raise TaxInvoiceCorrectionSourceError(
            f"{label} is missing, reversed, "
            "or has an active reversal child"
        )

    return row


async def _single_original_line(
    db: AsyncSession,
    *,
    statement,
    label: str,
) -> TaxInvoiceLine:
    rows = list(
        (
            await db.scalars(
                statement
            )
        ).all()
    )

    if len(rows) != 1:
        raise TaxInvoiceCorrectionSourceMappingError(
            f"{label} must map to exactly one original PN line; "
            f"found {len(rows)}"
        )

    return rows[0]


async def _line_for_tax_calculation(
    db: AsyncSession,
    *,
    company_id: int,
    original_tax_invoice_id: int,
    tax_calculation_id: int,
    label: str,
) -> TaxInvoiceLine:
    return await _single_original_line(
        db,
        statement=(
            select(
                TaxInvoiceLine
            )
            .where(
                TaxInvoiceLine.company_id
                == company_id,
                TaxInvoiceLine.tax_invoice_id
                == original_tax_invoice_id,
                TaxInvoiceLine.tax_calculation_id
                == tax_calculation_id,
            )
            .with_for_update()
        ),
        label=label,
    )


async def _resolve_sales_return(
    db: AsyncSession,
    *,
    company_id: int,
    original_tax_invoice_id: int,
    source_id: int,
) -> tuple[
    TaxInvoiceLine,
    TaxInvoiceCorrectionLineAmounts,
]:
    source = await _load_active_original(
        db,
        model=SalesReturnRecognitionEvent,
        company_id=company_id,
        source_id=source_id,
        label="sales return recognition",
    )

    sales_recognition = await _load_active_original(
        db,
        model=SalesRecognitionEvent,
        company_id=company_id,
        source_id=source.sales_recognition_event_id,
        label="sales recognition",
    )

    reversal = aliased(
        TaxRecognitionEvent
    )

    line = await _single_original_line(
        db,
        statement=(
            select(
                TaxInvoiceLine
            )
            .join(
                TaxRecognitionEvent,
                and_(
                    TaxRecognitionEvent.company_id
                    == TaxInvoiceLine.company_id,
                    TaxRecognitionEvent.id
                    == TaxInvoiceLine.tax_recognition_event_id,
                ),
            )
            .where(
                TaxInvoiceLine.company_id
                == company_id,
                TaxInvoiceLine.tax_invoice_id
                == original_tax_invoice_id,
                TaxRecognitionEvent
                .invoice_fulfillment_allocation_id
                == sales_recognition
                .invoice_fulfillment_allocation_id,
                TaxRecognitionEvent.reversal_of_id.is_(
                    None
                ),
                ~select(
                    reversal.id
                )
                .where(
                    reversal.company_id
                    == TaxRecognitionEvent.company_id,
                    reversal.reversal_of_id
                    == TaxRecognitionEvent.id,
                )
                .exists(),
            )
            .with_for_update()
        ),
        label="sales return",
    )

    amounts = _return_deltas(
        returned_quantity=(
            source.returned_quantity
        ),
        returned_gross_amount=(
            source.returned_gross_amount
        ),
        returned_tax_amount=(
            source.returned_tax_amount
        ),
    )

    return (
        line,
        amounts,
    )


async def _resolve_sales_value_correction(
    db: AsyncSession,
    *,
    company_id: int,
    original_tax_invoice_id: int,
    source_id: int,
) -> tuple[
    TaxInvoiceLine,
    TaxInvoiceCorrectionLineAmounts,
]:
    source = await _load_active_original(
        db,
        model=TradeValueCorrectionEvent,
        company_id=company_id,
        source_id=source_id,
        label="sales value correction",
    )

    if source.direction != "sale":
        raise TaxInvoiceCorrectionSourceError(
            "sales_value_correction requires direction='sale'"
        )

    line = await _single_original_line(
        db,
        statement=(
            select(
                TaxInvoiceLine
            )
            .join(
                TaxCalculation,
                and_(
                    TaxCalculation.company_id
                    == TaxInvoiceLine.company_id,
                    TaxCalculation.id
                    == TaxInvoiceLine.tax_calculation_id,
                ),
            )
            .where(
                TaxInvoiceLine.company_id
                == company_id,
                TaxInvoiceLine.tax_invoice_id
                == original_tax_invoice_id,
                TaxCalculation.trade_document_line_id
                == source.trade_document_line_id,
            )
            .with_for_update()
        ),
        label="sales value correction",
    )

    amounts = _value_deltas(
        original_gross_amount=(
            source.original_gross_amount
        ),
        original_tax_amount=(
            source.original_tax_amount
        ),
        corrected_gross_amount=(
            source.corrected_gross_amount
        ),
        corrected_tax_amount=(
            source.corrected_tax_amount
        ),
        original_quantity=(
            line.quantity
        ),
    )

    return (
        line,
        amounts,
    )


async def _resolve_purchase_return(
    db: AsyncSession,
    *,
    company_id: int,
    original_tax_invoice_id: int,
    source_id: int,
) -> tuple[
    TaxInvoiceLine,
    TaxInvoiceCorrectionLineAmounts,
]:
    source = await _load_active_original(
        db,
        model=PurchaseReturnVatAdjustmentEvent,
        company_id=company_id,
        source_id=source_id,
        label="purchase return VAT adjustment",
    )

    recognition = await _load_active_original(
        db,
        model=PurchaseReturnRecognitionEvent,
        company_id=company_id,
        source_id=(
            source.purchase_return_recognition_event_id
        ),
        label="purchase return recognition",
    )

    line = await _line_for_tax_calculation(
        db,
        company_id=company_id,
        original_tax_invoice_id=original_tax_invoice_id,
        tax_calculation_id=source.tax_calculation_id,
        label="purchase return",
    )

    base = abs(
        _decimal(
            source.adjusted_taxable_base
        )
    )

    tax = abs(
        _decimal(
            source.adjusted_tax_amount
        )
    )

    quantity = abs(
        _decimal(
            recognition.returned_quantity
        )
    )

    if quantity <= 0:
        raise TaxInvoiceCorrectionSourceError(
            "purchase return quantity must be positive"
        )

    if base == 0 and tax == 0:
        raise TaxInvoiceCorrectionSourceError(
            "purchase return VAT adjustment must not be zero"
        )

    amounts = TaxInvoiceCorrectionLineAmounts(
        quantity_delta=-quantity,
        unit_price_without_vat_delta=Decimal("0"),
        taxable_base_delta=-base,
        tax_amount_delta=-tax,
        total_with_vat_delta=(
            -base
            - tax
        ),
    )

    return (
        line,
        amounts,
    )


async def _resolve_purchase_value_correction(
    db: AsyncSession,
    *,
    company_id: int,
    original_tax_invoice_id: int,
    source_id: int,
) -> tuple[
    TaxInvoiceLine,
    TaxInvoiceCorrectionLineAmounts,
    PurchaseValueCorrectionVatAdjustmentEvent,
]:
    source = await _load_active_original(
        db,
        model=PurchaseValueCorrectionVatAdjustmentEvent,
        company_id=company_id,
        source_id=source_id,
        label="purchase value VAT adjustment",
    )

    line = await _line_for_tax_calculation(
        db,
        company_id=company_id,
        original_tax_invoice_id=original_tax_invoice_id,
        tax_calculation_id=source.tax_calculation_id,
        label="purchase value correction",
    )

    amounts = _purchase_adjustment_deltas(
        adjustment_kind=(
            source.adjustment_kind
        ),
        adjusted_taxable_base=(
            source.adjusted_taxable_base
        ),
        adjusted_tax_amount=(
            source.adjusted_tax_amount
        ),
        original_quantity=(
            line.quantity
        ),
    )

    return (
        line,
        amounts,
        source,
    )


async def _resolve_recognition_reversal(
    db: AsyncSession,
    *,
    company_id: int,
    original_tax_invoice_id: int,
    source_id: int,
) -> tuple[
    TaxInvoiceLine,
    TaxInvoiceCorrectionLineAmounts,
]:
    child = aliased(
        TaxRecognitionEvent
    )

    reversal = await db.scalar(
        select(
            TaxRecognitionEvent
        )
        .where(
            TaxRecognitionEvent.company_id
            == company_id,
            TaxRecognitionEvent.id
            == source_id,
            TaxRecognitionEvent.reversal_of_id.is_not(
                None
            ),
            ~select(
                child.id
            )
            .where(
                child.company_id
                == TaxRecognitionEvent.company_id,
                child.reversal_of_id
                == TaxRecognitionEvent.id,
            )
            .exists(),
        )
        .with_for_update()
    )

    if reversal is None:
        raise TaxInvoiceCorrectionSourceError(
            "recognition reversal source is missing "
            "or is not an active reversal"
        )

    original = await db.scalar(
        select(
            TaxRecognitionEvent
        )
        .where(
            TaxRecognitionEvent.company_id
            == company_id,
            TaxRecognitionEvent.id
            == reversal.reversal_of_id,
            TaxRecognitionEvent.reversal_of_id.is_(
                None
            ),
        )
        .with_for_update()
    )

    if original is None:
        raise TaxInvoiceCorrectionSourceError(
            "recognition reversal original event is missing"
        )

    if (
        original.tax_calculation_id
        != reversal.tax_calculation_id
    ):
        raise TaxInvoiceCorrectionSourceError(
            "recognition reversal tax calculation mismatch"
        )

    line = await _single_original_line(
        db,
        statement=(
            select(
                TaxInvoiceLine
            )
            .where(
                TaxInvoiceLine.company_id
                == company_id,
                TaxInvoiceLine.tax_invoice_id
                == original_tax_invoice_id,
                TaxInvoiceLine.tax_recognition_event_id
                == original.id,
            )
            .with_for_update()
        ),
        label="recognition reversal",
    )

    amounts = _recognition_reversal_deltas(
        original_line_quantity=(
            line.quantity
        ),
        recognized_taxable_base=(
            reversal.recognized_taxable_base
        ),
        recognized_tax_amount=(
            reversal.recognized_tax_amount
        ),
    )

    return (
        line,
        amounts,
    )


async def _validate_secondary_evidence(
    db: AsyncSession,
    *,
    company_id: int,
    original_invoice: TaxInvoice,
    original_line: TaxInvoiceLine,
    evidence_id: int | None,
    correction_document_number: str,
    correction_document_date: date,
    amounts: TaxInvoiceCorrectionLineAmounts,
    evidence_required: bool,
) -> int | None:
    if evidence_id is None:
        if evidence_required:
            raise TaxInvoiceCorrectionEvidenceError(
                "registered_adjustment evidence is required "
                "for an INPUT VAT increase"
            )

        return None

    if original_invoice.direction != "input":
        raise TaxInvoiceCorrectionEvidenceError(
            "TaxCreditEvidence is allowed only for INPUT RK"
        )

    evidence = await _load_active_original(
        db,
        model=TaxCreditEvidence,
        company_id=company_id,
        source_id=evidence_id,
        label="registered adjustment evidence",
    )

    if (
        str(
            evidence.evidence_type
        ).strip().lower()
        != "registered_adjustment"
    ):
        raise TaxInvoiceCorrectionEvidenceError(
            "RK legal evidence must be REGISTERED_ADJUSTMENT"
        )

    if (
        evidence.tax_calculation_id
        != original_line.tax_calculation_id
    ):
        raise TaxInvoiceCorrectionEvidenceError(
            "RK evidence tax calculation does not match "
            "the original PN line"
        )

    if (
        str(
            evidence.evidence_number
        ).strip()
        != correction_document_number
    ):
        raise TaxInvoiceCorrectionEvidenceError(
            "RK evidence number does not match canonical RK"
        )

    if (
        evidence.evidence_date
        != correction_document_date
    ):
        raise TaxInvoiceCorrectionEvidenceError(
            "RK evidence date does not match canonical RK"
        )

    if (
        abs(
            _decimal(
                evidence.evidenced_taxable_base
            )
        )
        != abs(
            amounts.taxable_base_delta
        )
    ):
        raise TaxInvoiceCorrectionEvidenceError(
            "RK evidence taxable base does not match line delta"
        )

    if (
        abs(
            _decimal(
                evidence.evidenced_tax_amount
            )
        )
        != abs(
            amounts.tax_amount_delta
        )
    ):
        raise TaxInvoiceCorrectionEvidenceError(
            "RK evidence VAT amount does not match line delta"
        )

    return evidence.id


def _resolved_line(
    *,
    source: TaxInvoiceCorrectionLineSource,
    original_line: TaxInvoiceLine,
    amounts: TaxInvoiceCorrectionLineAmounts,
    evidence_id: int | None,
) -> ResolvedTaxInvoiceCorrectionLine:
    ids = {
        "sales_return_recognition_event_id": None,
        "trade_value_correction_event_id": None,
        "purchase_return_vat_adjustment_event_id": None,
        "purchase_value_correction_vat_adjustment_event_id": None,
        "tax_recognition_reversal_event_id": None,
    }

    mapping = {
        "sales_return": (
            "sales_return_recognition_event_id"
        ),
        "sales_value_correction": (
            "trade_value_correction_event_id"
        ),
        "purchase_return": (
            "purchase_return_vat_adjustment_event_id"
        ),
        "purchase_value_correction": (
            "purchase_value_correction_vat_adjustment_event_id"
        ),
        "recognition_reversal": (
            "tax_recognition_reversal_event_id"
        ),
    }

    ids[
        mapping[
            source.source_kind
        ]
    ] = source.source_id

    return ResolvedTaxInvoiceCorrectionLine(
        line_number=source.line_number,
        original_tax_invoice_line_id=(
            original_line.id
        ),
        source_kind=source.source_kind,
        source_id=source.source_id,
        sales_return_recognition_event_id=(
            ids[
                "sales_return_recognition_event_id"
            ]
        ),
        trade_value_correction_event_id=(
            ids[
                "trade_value_correction_event_id"
            ]
        ),
        purchase_return_vat_adjustment_event_id=(
            ids[
                "purchase_return_vat_adjustment_event_id"
            ]
        ),
        purchase_value_correction_vat_adjustment_event_id=(
            ids[
                "purchase_value_correction_vat_adjustment_event_id"
            ]
        ),
        tax_recognition_reversal_event_id=(
            ids[
                "tax_recognition_reversal_event_id"
            ]
        ),
        tax_credit_evidence_id=evidence_id,
        reason_code=source.reason_code,
        description=_required_text(
            original_line.description,
            field="original PN line description",
            max_length=500,
        ),
        uom_code=_required_text(
            original_line.uom_code,
            field="original PN line UOM",
            max_length=20,
        ),
        classification_kind=_required_text(
            original_line.classification_kind,
            field="original PN classification",
            max_length=10,
        ),
        statutory_code=_required_text(
            original_line.statutory_code,
            field="original PN statutory code",
            max_length=32,
        ),
        tax_rate_code=_required_text(
            original_line.tax_rate_code,
            field="original PN tax rate code",
            max_length=50,
        ),
        tax_rate=_decimal(
            original_line.tax_rate
        ),
        quantity_delta=(
            amounts.quantity_delta
        ),
        unit_price_without_vat_delta=(
            amounts.unit_price_without_vat_delta
        ),
        taxable_base_delta=(
            amounts.taxable_base_delta
        ),
        tax_amount_delta=(
            amounts.tax_amount_delta
        ),
        total_with_vat_delta=(
            amounts.total_with_vat_delta
        ),
    )


async def resolve_tax_invoice_correction_lines(
    db: AsyncSession,
    *,
    company_id: int,
    original_invoice: TaxInvoice,
    correction_document_number: str,
    correction_document_date: date,
    sources: list[TaxInvoiceCorrectionLineSource],
) -> tuple[
    ResolvedTaxInvoiceCorrectionLine,
    ...,
]:
    company_id = _positive_int(
        company_id,
        field="company_id",
    )

    if original_invoice.company_id != company_id:
        raise TaxInvoiceCorrectionSourceError(
            "original PN does not belong to company"
        )

    normalized_sources = (
        normalize_correction_line_sources(
            sources
        )
    )

    resolved: list[
        ResolvedTaxInvoiceCorrectionLine
    ] = []

    for source in normalized_sources:
        validate_correction_source_kind_for_direction(
            direction=original_invoice.direction,
            source_kind=source.source_kind,
        )

        purchase_value_source = None

        if source.source_kind == "sales_return":
            (
                original_line,
                amounts,
            ) = await _resolve_sales_return(
                db,
                company_id=company_id,
                original_tax_invoice_id=(
                    original_invoice.id
                ),
                source_id=source.source_id,
            )

        elif (
            source.source_kind
            == "sales_value_correction"
        ):
            (
                original_line,
                amounts,
            ) = await _resolve_sales_value_correction(
                db,
                company_id=company_id,
                original_tax_invoice_id=(
                    original_invoice.id
                ),
                source_id=source.source_id,
            )

        elif source.source_kind == "purchase_return":
            (
                original_line,
                amounts,
            ) = await _resolve_purchase_return(
                db,
                company_id=company_id,
                original_tax_invoice_id=(
                    original_invoice.id
                ),
                source_id=source.source_id,
            )

        elif (
            source.source_kind
            == "purchase_value_correction"
        ):
            (
                original_line,
                amounts,
                purchase_value_source,
            ) = await _resolve_purchase_value_correction(
                db,
                company_id=company_id,
                original_tax_invoice_id=(
                    original_invoice.id
                ),
                source_id=source.source_id,
            )

        elif (
            source.source_kind
            == "recognition_reversal"
        ):
            (
                original_line,
                amounts,
            ) = await _resolve_recognition_reversal(
                db,
                company_id=company_id,
                original_tax_invoice_id=(
                    original_invoice.id
                ),
                source_id=source.source_id,
            )

        else:
            raise TaxInvoiceCorrectionSourceError(
                "unsupported RK source_kind"
            )

        evidence_required = bool(
            source.source_kind
            == "purchase_value_correction"
            and purchase_value_source is not None
            and str(
                purchase_value_source.adjustment_kind
            ).strip().lower()
            == "increase"
        )

        evidence_id = (
            await _validate_secondary_evidence(
                db,
                company_id=company_id,
                original_invoice=original_invoice,
                original_line=original_line,
                evidence_id=(
                    source.tax_credit_evidence_id
                ),
                correction_document_number=(
                    correction_document_number
                ),
                correction_document_date=(
                    correction_document_date
                ),
                amounts=amounts,
                evidence_required=evidence_required,
            )
        )

        resolved.append(
            _resolved_line(
                source=source,
                original_line=original_line,
                amounts=amounts,
                evidence_id=evidence_id,
            )
        )

    return tuple(
        resolved
    )
