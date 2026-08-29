from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tax_calculation import TaxCalculation
from app.models.trade_document import TradeDocument
from app.models.trade_document_line import TradeDocumentLine
from app.services.money_rounding import (
    round_currency_amount,
)
from app.services.tax_price_types import (
    TaxPriceMode,
)
from app.services.tax_rate_catalog import (
    TaxRateNotFoundError,
)
from app.services.tax_rate_definition import (
    TaxRateDefinition,
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
from app.services.ukrainian_vat_rate_catalog import (
    UKRAINIAN_VAT_RATE_CATALOG,
)


class InvoiceTaxCalculationError(Exception):
    """Base invoice VAT calculation error."""


class InvoiceTaxConfigurationError(
    InvoiceTaxCalculationError
):
    """Invoice line VAT configuration is invalid."""


class DuplicateInvoiceTaxCalculationError(
    InvoiceTaxCalculationError
):
    """Invoice already has persistent tax calculations."""


@dataclass(
    frozen=True,
    slots=True,
)
class InvoiceLineTaxComputation:
    tax_rate: TaxRateDefinition
    recognition_method: TaxRecognitionMethod
    taxable_base: Decimal
    tax_amount: Decimal
    gross_amount: Decimal


def _round(
    amount: Decimal,
    currency_code: str,
) -> Decimal:
    return round_currency_amount(
        amount=amount,
        currency_code=currency_code,
    )


def calculate_invoice_line_tax(
    *,
    document: TradeDocument,
    line: TradeDocumentLine,
) -> InvoiceLineTaxComputation | None:
    """
    Calculate one invoice-line VAT snapshot.

    No tax configuration:
        commercial amount is left untaxed.

    EXCLUSIVE:
        unit_price excludes VAT.

    INCLUSIVE:
        unit_price already includes VAT.
    """

    tax_rate_code = getattr(
        line,
        "tax_rate_code",
        None,
    )
    tax_recognition_method = getattr(
        line,
        "tax_recognition_method",
        None,
    )
    tax_price_mode = getattr(
        line,
        "tax_price_mode",
        None,
    )

    config = (
        tax_rate_code,
        tax_recognition_method,
        tax_price_mode,
    )

    if all(
        item is None
        for item in config
    ):
        return None

    if any(
        item is None
        for item in config
    ):
        raise InvoiceTaxConfigurationError(
            "Invoice line tax_rate_code, "
            "tax_recognition_method and "
            "tax_price_mode must be configured "
            "together"
        )

    rate_code = str(
        tax_rate_code
    ).strip().upper()

    if not rate_code:
        raise InvoiceTaxConfigurationError(
            "Invoice line tax rate code "
            "cannot be blank"
        )

    try:
        recognition_method = (
            TaxRecognitionMethod(
                tax_recognition_method
            )
        )
    except ValueError as exc:
        raise InvoiceTaxConfigurationError(
            "Unsupported invoice tax "
            "recognition method"
        ) from exc

    try:
        price_mode = TaxPriceMode(
            tax_price_mode
        )
    except ValueError as exc:
        raise InvoiceTaxConfigurationError(
            "Unsupported invoice tax price mode"
        ) from exc

    try:
        tax_rate = (
            UKRAINIAN_VAT_RATE_CATALOG
            .get_effective(
                rate_code,
                document.document_date,
            )
        )
    except TaxRateNotFoundError as exc:
        raise InvoiceTaxConfigurationError(
            "VAT rate is not effective for "
            f"invoice date: {rate_code}"
        ) from exc

    raw_commercial_amount = (
        Decimal(line.quantity)
        * Decimal(line.unit_price)
    )

    if price_mode == TaxPriceMode.EXCLUSIVE:
        taxable_base = _round(
            raw_commercial_amount,
            document.currency_code,
        )

        tax_amount = _round(
            taxable_base
            * tax_rate.rate,
            document.currency_code,
        )

        gross_amount = _round(
            taxable_base
            + tax_amount,
            document.currency_code,
        )

    elif price_mode == TaxPriceMode.INCLUSIVE:
        gross_amount = _round(
            raw_commercial_amount,
            document.currency_code,
        )

        if tax_rate.rate == Decimal("0"):
            taxable_base = gross_amount
            tax_amount = _round(
                Decimal("0"),
                document.currency_code,
            )

        else:
            taxable_base = _round(
                gross_amount
                / (
                    Decimal("1")
                    + tax_rate.rate
                ),
                document.currency_code,
            )

            # Derive VAT from rounded gross/base so
            # the accounting identity always holds:
            # gross = base + VAT.
            tax_amount = _round(
                gross_amount
                - taxable_base,
                document.currency_code,
            )

    else:
        raise InvoiceTaxConfigurationError(
            "Unsupported tax price mode"
        )

    return InvoiceLineTaxComputation(
        tax_rate=tax_rate,
        recognition_method=recognition_method,
        taxable_base=taxable_base,
        tax_amount=tax_amount,
        gross_amount=gross_amount,
    )


def calculate_invoice_payable_total(
    document: TradeDocument,
) -> Decimal:
    """
    Calculate tax-inclusive commercial obligation.

    Tax-free/unconfigured line:
        quantity * unit_price

    VAT EXCLUSIVE:
        base + VAT

    VAT INCLUSIVE:
        quantity * unit_price already includes VAT
    """

    if document.kind != TradeDocumentKind.INVOICE:
        raise InvoiceTaxConfigurationError(
            "Only Trade Invoice has an "
            "invoice payable total"
        )

    total = Decimal("0")

    for line in document.lines:
        calculation = (
            calculate_invoice_line_tax(
                document=document,
                line=line,
            )
        )

        if calculation is None:
            line_amount = _round(
                Decimal(line.quantity)
                * Decimal(line.unit_price),
                document.currency_code,
            )
        else:
            line_amount = (
                calculation.gross_amount
            )

        total += line_amount

    return _round(
        total,
        document.currency_code,
    )


def build_invoice_tax_calculation(
    *,
    document: TradeDocument,
    line: TradeDocumentLine,
) -> TaxCalculation | None:
    calculation = calculate_invoice_line_tax(
        document=document,
        line=line,
    )

    if calculation is None:
        return None

    if document.id is None:
        raise InvoiceTaxConfigurationError(
            "Persistent invoice ID is required"
        )

    if line.id is None:
        raise InvoiceTaxConfigurationError(
            "Persistent invoice line ID is required"
        )

    if (
        line.company_id
        != document.company_id
    ):
        raise InvoiceTaxConfigurationError(
            "Invoice line company mismatch"
        )

    if (
        line.trade_document_id
        != document.id
    ):
        raise InvoiceTaxConfigurationError(
            "Invoice line source mismatch"
        )

    if document.direction == TradeDirection.SALE:
        tax_direction = TaxDirection.OUTPUT

    elif (
        document.direction
        == TradeDirection.PURCHASE
    ):
        tax_direction = TaxDirection.INPUT

    else:
        raise InvoiceTaxConfigurationError(
            "Unsupported Trade Invoice direction"
        )

    return TaxCalculation(
        company_id=document.company_id,
        trade_document_id=document.id,
        trade_document_line_id=line.id,
        product_id=line.product_id,
        tax_type=(
            calculation.tax_rate.tax_type
        ),
        direction=tax_direction,
        tax_rate_code=(
            calculation.tax_rate.code
        ),
        tax_rate=(
            calculation.tax_rate.rate
        ),
        treatment=(
            calculation.tax_rate.treatment
        ),
        recognition_method=(
            calculation.recognition_method
        ),
        taxable_base=(
            calculation.taxable_base
        ),
        tax_amount=(
            calculation.tax_amount
        ),
        currency_code=document.currency_code,
        calculation_date=(
            document.document_date
        ),
    )


async def create_tax_calculations_for_invoice(
    db: AsyncSession,
    *,
    document: TradeDocument,
) -> tuple[TaxCalculation, ...]:
    """
    Persist immutable VAT snapshots for one confirmed invoice.

    Caller owns transaction.
    """

    if document.kind != TradeDocumentKind.INVOICE:
        raise InvoiceTaxConfigurationError(
            "Only Trade Invoice can create "
            "tax calculations"
        )

    if (
        document.status
        != TradeDocumentStatus.CONFIRMED
    ):
        raise InvoiceTaxConfigurationError(
            "Only confirmed Trade Invoice can "
            "create tax calculations"
        )

    calculations = tuple(
        calculation
        for line in sorted(
            document.lines,
            key=lambda item: item.line_number,
        )
        if (
            calculation
            := build_invoice_tax_calculation(
                document=document,
                line=line,
            )
        )
        is not None
    )

    # Preserve old non-VAT invoice flow:
    # no VAT config means no additional DB query,
    # no TaxCalculation and no behavioral change.
    if not calculations:
        return ()

    existing = (
        await db.execute(
            select(
                TaxCalculation.id
            )
            .where(
                TaxCalculation.company_id
                == document.company_id,
                TaxCalculation.trade_document_id
                == document.id,
            )
            .limit(1)
        )
    ).scalar_one_or_none()

    if existing is not None:
        raise (
            DuplicateInvoiceTaxCalculationError(
                "Trade Invoice already has "
                "persistent tax calculations"
            )
        )

    for calculation in calculations:
        db.add(
            calculation
        )

    await db.flush()

    return calculations
