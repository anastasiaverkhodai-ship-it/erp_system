from decimal import Decimal

import pytest

from app.services.sales_profitability_calculation_service import (
    SalesGrossProfitabilityProjection,
    calculate_sales_gross_profitability,
)


def test_basic_sales_gross_profitability():
    result = calculate_sales_gross_profitability(
        recognized_gross_amount=Decimal("120.00"),
        recognized_tax_amount=Decimal("20.00"),
        inventory_cost_amount=Decimal("60.00"),
    )

    assert result == SalesGrossProfitabilityProjection(
        net_revenue=Decimal("100.00"),
        cogs=Decimal("60.00"),
        gross_profit=Decimal("40.00"),
        gross_margin_percent=Decimal("40.0000"),
    )


def test_sales_return_reduces_revenue_and_restores_cogs():
    result = calculate_sales_gross_profitability(
        recognized_gross_amount=Decimal("240.00"),
        recognized_tax_amount=Decimal("40.00"),
        returned_net_revenue_amount=Decimal("50.00"),
        inventory_cost_amount=Decimal("120.00"),
        restored_cost_amount=Decimal("30.00"),
    )

    assert result.net_revenue == Decimal("150.00")
    assert result.cogs == Decimal("90.00")
    assert result.gross_profit == Decimal("60.00")
    assert result.gross_margin_percent == Decimal("40.0000")


def test_full_return_has_null_margin_when_revenue_is_zero():
    result = calculate_sales_gross_profitability(
        recognized_gross_amount=Decimal("120.00"),
        recognized_tax_amount=Decimal("20.00"),
        returned_net_revenue_amount=Decimal("100.00"),
        inventory_cost_amount=Decimal("60.00"),
        restored_cost_amount=Decimal("60.00"),
    )

    assert result.net_revenue == Decimal("0.00")
    assert result.cogs == Decimal("0.00")
    assert result.gross_profit == Decimal("0.00")
    assert result.gross_margin_percent is None


def test_loss_sale_produces_negative_margin():
    result = calculate_sales_gross_profitability(
        recognized_gross_amount=Decimal("100.00"),
        recognized_tax_amount=Decimal("0.00"),
        inventory_cost_amount=Decimal("125.00"),
    )

    assert result.net_revenue == Decimal("100.00")
    assert result.cogs == Decimal("125.00")
    assert result.gross_profit == Decimal("-25.00")
    assert result.gross_margin_percent == Decimal("-25.0000")


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        (
            {
                "recognized_gross_amount": Decimal("-1.00"),
                "recognized_tax_amount": Decimal("0.00"),
                "inventory_cost_amount": Decimal("0.00"),
            },
            "recognized_gross_amount",
        ),
        (
            {
                "recognized_gross_amount": Decimal("100.00"),
                "recognized_tax_amount": Decimal("101.00"),
                "inventory_cost_amount": Decimal("0.00"),
            },
            "recognized_tax_amount",
        ),
        (
            {
                "recognized_gross_amount": Decimal("100.00"),
                "recognized_tax_amount": Decimal("0.00"),
                "returned_net_revenue_amount": Decimal("101.00"),
                "inventory_cost_amount": Decimal("0.00"),
            },
            "returned_net_revenue_amount",
        ),
        (
            {
                "recognized_gross_amount": Decimal("100.00"),
                "recognized_tax_amount": Decimal("0.00"),
                "inventory_cost_amount": Decimal("50.00"),
                "restored_cost_amount": Decimal("51.00"),
            },
            "restored_cost_amount",
        ),
    ],
)
def test_invalid_inputs_fail_closed(kwargs, message):
    with pytest.raises(ValueError, match=message):
        calculate_sales_gross_profitability(**kwargs)
