import inspect

import pytest

from app.api.v1 import trade_documents as api
from app.services.sales_pricing_service import (
    SalesPricingConfigurationError,
    resolve_sales_master_price,
)
from app.services.trade_document_types import (
    TradeDirection,
)


@pytest.mark.asyncio
async def test_sales_pricing_rejects_purchase_before_db_access():
    with pytest.raises(
        SalesPricingConfigurationError,
        match="only for sale documents",
    ):
        await resolve_sales_master_price(
            None,
            company_id=1,
            product_id=1,
            price_type_code="RETAIL",
            document_date=__import__(
                "datetime"
            ).date(2026, 9, 14),
            document_currency_code="UAH",
            document_direction=(
                TradeDirection.PURCHASE
            ),
        )


def test_create_trade_document_wires_price_snapshot():
    source = inspect.getsource(
        api.create_trade_document
    )

    assert (
        "_resolve_trade_line_price_snapshot"
        in source
    )
    assert (
        'price_snapshot["unit_price"]'
        in source
    )
    assert (
        '"source_product_price_id"'
        in source
    )
    assert (
        '"price_effective_from"'
        in source
    )


def test_update_resolves_before_deleting_old_lines():
    source = inspect.getsource(
        api.update_trade_document
    )

    resolution_position = source.index(
        "resolved_line_prices = ["
    )

    deletion_position = source.index(
        "delete("
    )

    assert (
        resolution_position
        < deletion_position
    )


def test_update_wires_price_snapshot():
    source = inspect.getsource(
        api.update_trade_document
    )

    assert (
        'price_snapshot['
        in source
    )
    assert (
        '"price_type_code"'
        in source
    )
    assert (
        '"source_product_price_id"'
        in source
    )
    assert (
        '"price_uom_code"'
        in source
    )
    assert (
        '"price_effective_from"'
        in source
    )


def test_pricing_resolver_owns_no_transaction():
    source = inspect.getsource(
        resolve_sales_master_price
    )

    assert ".commit(" not in source
    assert ".rollback(" not in source
