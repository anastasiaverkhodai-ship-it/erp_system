from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.services.sales_credit_control_service import (
    SalesCreditDataIntegrityError,
    SalesCreditExposure,
    SalesCreditPolicy,
    calculate_sales_order_amount,
    _is_limit_enabled,
)
from app.services.trade_document_types import (
    TradeDirection,
    TradeDocumentKind,
)


def _document(
    *,
    direction=TradeDirection.SALE,
    kind=TradeDocumentKind.ORDER,
    lines=None,
    currency_code="UAH",
):
    return SimpleNamespace(
        direction=direction,
        kind=kind,
        currency_code=currency_code,
        lines=(
            lines
            if lines is not None
            else []
        ),
    )


def _line(
    quantity: str,
    unit_price: str,
):
    return SimpleNamespace(
        quantity=Decimal(quantity),
        unit_price=Decimal(unit_price),
    )


def test_sales_order_amount_is_sum_of_line_values() -> None:
    document = _document(
        lines=[
            _line("2", "100.50"),
            _line("3", "10.00"),
        ]
    )

    assert (
        calculate_sales_order_amount(document)
        == Decimal("231.00")
    )


def test_empty_sales_order_amount_is_zero() -> None:
    assert (
        calculate_sales_order_amount(
            _document()
        )
        == Decimal("0")
    )


def test_non_sales_order_rejected() -> None:
    document = _document(
        direction=TradeDirection.PURCHASE,
    )

    with pytest.raises(
        SalesCreditDataIntegrityError
    ):
        calculate_sales_order_amount(document)


def test_zero_limit_is_not_configured() -> None:
    assert not _is_limit_enabled(
        Decimal("0")
    )


def test_positive_limit_is_enabled() -> None:
    assert _is_limit_enabled(
        Decimal("1000")
    )


def test_exposure_before_and_after() -> None:
    exposure = SalesCreditExposure(
        receivable_open_amount=Decimal("300"),
        residual_order_commitment=Decimal("200"),
        requested_order_amount=Decimal("250"),
    )

    assert (
        exposure.exposure_before
        == Decimal("500")
    )

    assert (
        exposure.exposure_after
        == Decimal("750")
    )


def test_policy_keeps_both_limits() -> None:
    policy = SalesCreditPolicy(
        counterparty_limit=Decimal("5000"),
        contract_limit=Decimal("2000"),
    )

    assert (
        policy.counterparty_limit
        == Decimal("5000")
    )

    assert (
        policy.contract_limit
        == Decimal("2000")
    )


def test_sales_order_amount_vat_exclusive_is_gross() -> None:
    from datetime import date

    from app.services.tax_price_types import (
        TaxPriceMode,
    )
    from app.services.tax_recognition_types import (
        TaxRecognitionMethod,
    )

    document = _document(
        lines=[
            SimpleNamespace(
                quantity=Decimal("10"),
                unit_price=Decimal("100"),
                tax_rate_code="VAT20",
                tax_recognition_method=(
                    TaxRecognitionMethod.FIRST_EVENT
                ),
                tax_price_mode=TaxPriceMode.EXCLUSIVE,
            )
        ]
    )
    document.document_date = date(2026, 1, 15)
    document.currency_code = "UAH"

    assert (
        calculate_sales_order_amount(document)
        == Decimal("1200.00")
    )


def test_sales_order_amount_vat_inclusive_is_gross() -> None:
    from datetime import date

    from app.services.tax_price_types import (
        TaxPriceMode,
    )
    from app.services.tax_recognition_types import (
        TaxRecognitionMethod,
    )

    document = _document(
        lines=[
            SimpleNamespace(
                quantity=Decimal("10"),
                unit_price=Decimal("120"),
                tax_rate_code="VAT20",
                tax_recognition_method=(
                    TaxRecognitionMethod.FIRST_EVENT
                ),
                tax_price_mode=TaxPriceMode.INCLUSIVE,
            )
        ]
    )
    document.document_date = date(2026, 1, 15)
    document.currency_code = "UAH"

    assert (
        calculate_sales_order_amount(document)
        == Decimal("1200.00")
    )


def test_sales_order_amount_untaxed_is_commercial_total() -> None:
    from datetime import date

    document = _document(
        lines=[
            SimpleNamespace(
                quantity=Decimal("3"),
                unit_price=Decimal("33.3333"),
                tax_rate_code=None,
                tax_recognition_method=None,
                tax_price_mode=None,
            )
        ]
    )
    document.document_date = date(2026, 1, 15)
    document.currency_code = "UAH"

    assert (
        calculate_sales_order_amount(document)
        == Decimal("100.00")
    )


@pytest.mark.asyncio
async def test_receivable_exposure_rejects_mixed_currency():
    from types import SimpleNamespace

    from app.services.sales_credit_control_service import (
        SalesCreditDataIntegrityError,
        _get_receivable_open_amount,
    )

    class _Scalars:
        def all(self):
            return [
                SimpleNamespace(
                    id=1,
                    currency_code="EUR",
                    original_amount=Decimal("100.00"),
                )
            ]

    class _Result:
        def scalars(self):
            return _Scalars()

    class _DB:
        async def execute(self, statement):
            return _Result()

    with pytest.raises(
        SalesCreditDataIntegrityError,
        match="different currency",
    ):
        await _get_receivable_open_amount(
            _DB(),
            company_id=1,
            counterparty_id=2,
            contract_id=None,
            restrict_contract=False,
            currency_code="UAH",
        )


@pytest.mark.asyncio
async def test_residual_order_exposure_rejects_mixed_currency():
    from datetime import date

    from app.services.sales_credit_control_service import (
        SalesCreditDataIntegrityError,
        _get_residual_order_commitment,
    )

    class _Result:
        def all(self):
            return [
                (
                    10,
                    Decimal("1.0000"),
                    Decimal("100.0000"),
                    None,
                    None,
                    None,
                    date(2026, 1, 15),
                    "EUR",
                    Decimal("0.0000"),
                )
            ]

    class _DB:
        async def execute(self, statement):
            return _Result()

    with pytest.raises(
        SalesCreditDataIntegrityError,
        match="different currency",
    ):
        await _get_residual_order_commitment(
            _DB(),
            company_id=1,
            counterparty_id=2,
            contract_id=None,
            restrict_contract=False,
            exclude_order_id=99,
            currency_code="UAH",
        )
