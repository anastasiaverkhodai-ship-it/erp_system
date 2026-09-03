from dataclasses import dataclass
from decimal import (
    Decimal,
    ROUND_HALF_UP,
)

from app.models.trade_document import TradeDocument
from app.models.trade_document_line import TradeDocumentLine
from app.services.invoice_tax_calculation_service import (
    InvoiceTaxCalculationError,
    calculate_invoice_line_tax,
)
from app.services.money_rounding import (
    round_currency_amount,
)
from app.services.trade_document_types import (
    TradeDirection,
    TradeDocumentKind,
)


ZERO = Decimal("0")
PRICE_QUANTUM = Decimal("0.0001")


class PurchaseReceiptAccountingPriceError(Exception):
    """Purchase receipt accounting-price calculation failed."""


class PurchaseReceiptAccountingSourceError(
    PurchaseReceiptAccountingPriceError
):
    """Purchase Order source state is invalid."""


class PurchaseReceiptAccountingAllocationError(
    PurchaseReceiptAccountingPriceError
):
    """Requested receipt quantity interval is invalid."""


@dataclass(
    frozen=True,
    slots=True,
)
class PurchaseReceiptAccountingSlice:
    """
    VAT-exclusive accounting value for one Purchase Order
    fulfillment slice.

    amount
        Exact currency-rounded taxable-base amount allocated
        to this receipt slice.

    unit_price
        Four-decimal warehouse receipt price whose
        currency-rounded line amount must equal ``amount``.
    """

    amount: Decimal
    unit_price: Decimal


def _price(
    value: Decimal,
) -> Decimal:
    return Decimal(
        value
    ).quantize(
        PRICE_QUANTUM,
        rounding=ROUND_HALF_UP,
    )


def _money(
    value: Decimal,
    *,
    currency_code: str,
) -> Decimal:
    return round_currency_amount(
        amount=Decimal(
            value
        ),
        currency_code=currency_code,
    )


def _resolve_full_taxable_base(
    *,
    document: TradeDocument,
    line: TradeDocumentLine,
) -> Decimal:
    """
    Reuse the existing canonical VAT line calculator.

    Although the existing function is named
    ``calculate_invoice_line_tax``, its calculation core is pure:
    it reads line VAT configuration, document date/currency, and
    returns the same taxable-base semantics needed here.

    No second EXCLUSIVE / INCLUSIVE VAT formula lives here.
    """
    try:
        calculation = calculate_invoice_line_tax(
            document=document,
            line=line,
        )
    except InvoiceTaxCalculationError as exc:
        raise PurchaseReceiptAccountingSourceError(
            "Purchase Order VAT configuration cannot be "
            f"converted to receipt accounting value: {exc}"
        ) from exc

    if calculation is not None:
        return Decimal(
            calculation.taxable_base
        )

    return _money(
        Decimal(
            line.quantity
        )
        * Decimal(
            line.unit_price
        ),
        currency_code=document.currency_code,
    )


def _cumulative_amount(
    *,
    total_amount: Decimal,
    fulfilled_quantity: Decimal,
    source_quantity: Decimal,
    currency_code: str,
) -> Decimal:
    if fulfilled_quantity == ZERO:
        return _money(
            ZERO,
            currency_code=currency_code,
        )

    if fulfilled_quantity == source_quantity:
        return Decimal(
            total_amount
        )

    return _money(
        Decimal(
            total_amount
        )
        * Decimal(
            fulfilled_quantity
        )
        / Decimal(
            source_quantity
        ),
        currency_code=currency_code,
    )


def calculate_purchase_receipt_accounting_slice(
    *,
    document: TradeDocument,
    line: TradeDocumentLine,
    fulfilled_before: Decimal,
    fulfilled_after: Decimal,
) -> PurchaseReceiptAccountingSlice:
    """
    Allocate VAT-exclusive purchase value to one receipt slice.

    The full Purchase Order line taxable base comes from the
    canonical VAT calculator:

        EXCLUSIVE -> raw commercial amount is already base
        INCLUSIVE -> VAT is extracted and taxable base returned
        no tax     -> raw line amount

    Partial receipts use cumulative-delta allocation so the sum
    of every receipt slice equals the exact currency-rounded
    full taxable base.

    Example:

        quantity = 3
        inclusive unit price = 100
        VAT20

        gross = 300
        taxable base = 250

        receipt slices:
            83.33
            83.34
            83.33

        total = 250.00
    """
    if document.direction != TradeDirection.PURCHASE:
        raise PurchaseReceiptAccountingSourceError(
            "Receipt accounting price requires "
            "a PURCHASE TradeDocument"
        )

    if document.kind != TradeDocumentKind.ORDER:
        raise PurchaseReceiptAccountingSourceError(
            "Receipt accounting price requires "
            "a Purchase Order"
        )

    source_quantity = Decimal(
        line.quantity
    )

    if source_quantity <= ZERO:
        raise PurchaseReceiptAccountingSourceError(
            "Purchase Order line quantity must be "
            "greater than zero"
        )

    before = Decimal(
        fulfilled_before
    )
    after = Decimal(
        fulfilled_after
    )

    if before < ZERO:
        raise PurchaseReceiptAccountingAllocationError(
            "fulfilled_before cannot be negative"
        )

    if after <= before:
        raise PurchaseReceiptAccountingAllocationError(
            "fulfilled_after must be greater than "
            "fulfilled_before"
        )

    if after > source_quantity:
        raise PurchaseReceiptAccountingAllocationError(
            "fulfilled_after cannot exceed "
            "Purchase Order line quantity"
        )

    full_base = _resolve_full_taxable_base(
        document=document,
        line=line,
    )

    cumulative_before = _cumulative_amount(
        total_amount=full_base,
        fulfilled_quantity=before,
        source_quantity=source_quantity,
        currency_code=document.currency_code,
    )

    cumulative_after = _cumulative_amount(
        total_amount=full_base,
        fulfilled_quantity=after,
        source_quantity=source_quantity,
        currency_code=document.currency_code,
    )

    slice_amount = (
        cumulative_after
        - cumulative_before
    )

    receipt_quantity = (
        after
        - before
    )

    if slice_amount < ZERO:
        raise PurchaseReceiptAccountingAllocationError(
            "Calculated receipt accounting amount "
            "cannot be negative"
        )

    if slice_amount == ZERO:
        unit_price = _price(
            ZERO
        )
    else:
        unit_price = _price(
            slice_amount
            / receipt_quantity
        )

    represented_amount = _money(
        unit_price
        * receipt_quantity,
        currency_code=document.currency_code,
    )

    if represented_amount != slice_amount:
        raise PurchaseReceiptAccountingAllocationError(
            "Four-decimal warehouse receipt price cannot "
            "represent the allocated taxable-base amount "
            "without currency rounding drift: "
            f"allocated={slice_amount}, "
            f"represented={represented_amount}, "
            f"quantity={receipt_quantity}, "
            f"unit_price={unit_price}"
        )

    return PurchaseReceiptAccountingSlice(
        amount=slice_amount,
        unit_price=unit_price,
    )
