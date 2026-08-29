from datetime import date
from decimal import Decimal
from unittest.mock import (
    AsyncMock,
    Mock,
)

import pytest

from app.models.tax_calculation import (
    TaxCalculation,
)
from app.models.trade_document import (
    TradeDocument,
)
from app.models.trade_document_line import (
    TradeDocumentLine,
)
from app.services.counterparty_open_item_service import (
    calculate_invoice_open_item_amount,
)
from app.services.invoice_tax_calculation_service import (
    DuplicateInvoiceTaxCalculationError,
    InvoiceTaxConfigurationError,
    build_invoice_tax_calculation,
    calculate_invoice_line_tax,
    calculate_invoice_payable_total,
    create_tax_calculations_for_invoice,
)
from app.services.tax_price_types import (
    TaxPriceMode,
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


def _document(
    *,
    direction=TradeDirection.SALE,
    document_date=date(2026, 8, 29),
) -> TradeDocument:
    return TradeDocument(
        id=100,
        company_id=1,
        counterparty_id=1,
        contract_id=None,
        number="VAT-TEST",
        direction=direction,
        kind=TradeDocumentKind.INVOICE,
        status=TradeDocumentStatus.CONFIRMED,
        document_date=document_date,
        currency_code="UAH",
        payment_term_days=0,
        created_by=1,
    )


def _line(
    *,
    line_id=200,
    line_number=1,
    quantity="1.0000",
    unit_price="100.0000",
    tax_rate_code="VAT20",
    recognition_method=(
        TaxRecognitionMethod.FIRST_EVENT
    ),
    price_mode=TaxPriceMode.EXCLUSIVE,
) -> TradeDocumentLine:
    return TradeDocumentLine(
        id=line_id,
        company_id=1,
        trade_document_id=100,
        line_number=line_number,
        product_id=line_id,
        warehouse_id=None,
        quantity=Decimal(quantity),
        unit_price=Decimal(unit_price),
        tax_rate_code=tax_rate_code,
        tax_recognition_method=(
            recognition_method
        ),
        tax_price_mode=price_mode,
    )


def _attach(
    document: TradeDocument,
    *lines: TradeDocumentLine,
) -> None:
    document.lines = list(lines)


def test_exclusive_vat20():
    document = _document()

    line = _line(
        quantity="2.0000",
        unit_price="100.0000",
        price_mode=TaxPriceMode.EXCLUSIVE,
    )

    result = calculate_invoice_line_tax(
        document=document,
        line=line,
    )

    assert result is not None
    assert (
        result.taxable_base
        == Decimal("200.00")
    )
    assert (
        result.tax_amount
        == Decimal("40.00")
    )
    assert (
        result.gross_amount
        == Decimal("240.00")
    )


def test_inclusive_vat20():
    document = _document()

    line = _line(
        unit_price="120.0000",
        price_mode=TaxPriceMode.INCLUSIVE,
    )

    result = calculate_invoice_line_tax(
        document=document,
        line=line,
    )

    assert result is not None
    assert (
        result.taxable_base
        == Decimal("100.00")
    )
    assert (
        result.tax_amount
        == Decimal("20.00")
    )
    assert (
        result.gross_amount
        == Decimal("120.00")
    )


def test_inclusive_vat7():
    document = _document()

    line = _line(
        unit_price="107.0000",
        tax_rate_code="VAT7",
        price_mode=TaxPriceMode.INCLUSIVE,
    )

    result = calculate_invoice_line_tax(
        document=document,
        line=line,
    )

    assert result is not None
    assert (
        result.taxable_base
        == Decimal("100.00")
    )
    assert (
        result.tax_amount
        == Decimal("7.00")
    )
    assert (
        result.gross_amount
        == Decimal("107.00")
    )


def test_zero_rated_vat():
    document = _document()

    line = _line(
        unit_price="100.0000",
        tax_rate_code="VAT0",
        price_mode=TaxPriceMode.EXCLUSIVE,
    )

    result = calculate_invoice_line_tax(
        document=document,
        line=line,
    )

    assert result is not None
    assert (
        result.taxable_base
        == Decimal("100.00")
    )
    assert (
        result.tax_amount
        == Decimal("0.00")
    )
    assert (
        result.gross_amount
        == Decimal("100.00")
    )


def test_unconfigured_line_has_no_tax_snapshot():
    document = _document()

    line = _line(
        tax_rate_code=None,
        recognition_method=None,
        price_mode=None,
    )

    assert (
        calculate_invoice_line_tax(
            document=document,
            line=line,
        )
        is None
    )


def test_vat14_before_effective_date_rejected():
    document = _document(
        document_date=date(2021, 2, 28)
    )

    line = _line(
        tax_rate_code="VAT14",
    )

    with pytest.raises(
        InvoiceTaxConfigurationError
    ):
        calculate_invoice_line_tax(
            document=document,
            line=line,
        )


def test_sale_builds_output_tax_snapshot():
    document = _document(
        direction=TradeDirection.SALE
    )

    line = _line()

    calculation = (
        build_invoice_tax_calculation(
            document=document,
            line=line,
        )
    )

    assert isinstance(
        calculation,
        TaxCalculation,
    )
    assert (
        calculation.direction
        == TaxDirection.OUTPUT
    )
    assert (
        calculation.taxable_base
        == Decimal("100.00")
    )
    assert (
        calculation.tax_amount
        == Decimal("20.00")
    )


def test_purchase_builds_input_tax_snapshot():
    document = _document(
        direction=TradeDirection.PURCHASE
    )

    line = _line()

    calculation = (
        build_invoice_tax_calculation(
            document=document,
            line=line,
        )
    )

    assert calculation is not None
    assert (
        calculation.direction
        == TaxDirection.INPUT
    )


def test_invoice_payable_total_is_tax_inclusive():
    document = _document()

    exclusive = _line(
        line_id=201,
        line_number=1,
        unit_price="100.0000",
        tax_rate_code="VAT20",
        price_mode=TaxPriceMode.EXCLUSIVE,
    )

    inclusive = _line(
        line_id=202,
        line_number=2,
        unit_price="107.0000",
        tax_rate_code="VAT7",
        price_mode=TaxPriceMode.INCLUSIVE,
    )

    no_tax = _line(
        line_id=203,
        line_number=3,
        unit_price="50.0000",
        tax_rate_code=None,
        recognition_method=None,
        price_mode=None,
    )

    _attach(
        document,
        exclusive,
        inclusive,
        no_tax,
    )

    assert (
        calculate_invoice_payable_total(
            document
        )
        == Decimal("277.00")
    )

    assert (
        calculate_invoice_open_item_amount(
            document
        )
        == Decimal("277.00")
    )


@pytest.mark.asyncio
async def test_persist_tax_calculation():
    document = _document()
    line = _line()

    _attach(
        document,
        line,
    )

    result = Mock()
    result.scalar_one_or_none.return_value = None

    db = Mock()
    db.execute = AsyncMock(
        return_value=result
    )
    db.add = Mock()
    db.flush = AsyncMock()

    calculations = (
        await create_tax_calculations_for_invoice(
            db,
            document=document,
        )
    )

    assert len(calculations) == 1

    stored = calculations[0]

    assert stored.tax_rate_code == "VAT20"
    assert (
        stored.recognition_method
        == TaxRecognitionMethod.FIRST_EVENT
    )

    db.add.assert_called_once_with(
        stored
    )
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_no_tax_invoice_preserves_old_flow():
    document = _document()

    line = _line(
        tax_rate_code=None,
        recognition_method=None,
        price_mode=None,
    )

    _attach(
        document,
        line,
    )

    db = Mock()
    db.execute = AsyncMock()
    db.add = Mock()
    db.flush = AsyncMock()

    result = (
        await create_tax_calculations_for_invoice(
            db,
            document=document,
        )
    )

    assert result == ()

    db.execute.assert_not_awaited()
    db.add.assert_not_called()
    db.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_duplicate_invoice_tax_rejected():
    document = _document()
    line = _line()

    _attach(
        document,
        line,
    )

    result = Mock()
    result.scalar_one_or_none.return_value = 999

    db = Mock()
    db.execute = AsyncMock(
        return_value=result
    )
    db.add = Mock()
    db.flush = AsyncMock()

    with pytest.raises(
        DuplicateInvoiceTaxCalculationError
    ):
        await create_tax_calculations_for_invoice(
            db,
            document=document,
        )

    db.add.assert_not_called()
