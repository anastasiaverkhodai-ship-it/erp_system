from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP


MONEY_QUANTUM = Decimal("0.01")
PERCENT_QUANTUM = Decimal("0.0001")
ZERO = Decimal("0")


def _decimal(value: Decimal | int | str) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _money(value: Decimal | int | str) -> Decimal:
    return _decimal(value).quantize(
        MONEY_QUANTUM,
        rounding=ROUND_HALF_UP,
    )


def _percent(value: Decimal) -> Decimal:
    return value.quantize(
        PERCENT_QUANTUM,
        rounding=ROUND_HALF_UP,
    )


@dataclass(frozen=True)
class SalesGrossProfitabilityProjection:
    net_revenue: Decimal
    cogs: Decimal
    gross_profit: Decimal
    gross_margin_percent: Decimal | None


def calculate_sales_gross_profitability(
    *,
    recognized_gross_amount: Decimal,
    recognized_tax_amount: Decimal,
    returned_net_revenue_amount: Decimal = ZERO,
    inventory_cost_amount: Decimal,
    restored_cost_amount: Decimal = ZERO,
) -> SalesGrossProfitabilityProjection:
    """
    Calculate a read-only sales gross-profitability projection.

    Canonical economic inputs are immutable monetary snapshots produced
    elsewhere in the Sales / Warehouse lifecycle.

    This function:
    - does not mutate operational or accounting state;
    - does not perform FX conversion;
    - does not infer VAT;
    - does not reconstruct historical prices or costs;
    - does not persist a profitability ledger.
    """

    gross = _money(recognized_gross_amount)
    tax = _money(recognized_tax_amount)
    returned_net = _money(returned_net_revenue_amount)
    cost = _money(inventory_cost_amount)
    restored_cost = _money(restored_cost_amount)

    for label, value in (
        ("recognized_gross_amount", gross),
        ("recognized_tax_amount", tax),
        ("returned_net_revenue_amount", returned_net),
        ("inventory_cost_amount", cost),
        ("restored_cost_amount", restored_cost),
    ):
        if value < ZERO:
            raise ValueError(f"{label} must not be negative")

    if tax > gross:
        raise ValueError(
            "recognized_tax_amount must not exceed "
            "recognized_gross_amount"
        )

    original_net_revenue = _money(gross - tax)

    if returned_net > original_net_revenue:
        raise ValueError(
            "returned_net_revenue_amount must not exceed "
            "original net revenue"
        )

    if restored_cost > cost:
        raise ValueError(
            "restored_cost_amount must not exceed "
            "inventory_cost_amount"
        )

    net_revenue = _money(
        original_net_revenue - returned_net
    )
    cogs = _money(cost - restored_cost)
    gross_profit = _money(net_revenue - cogs)

    if net_revenue == ZERO:
        gross_margin_percent = None
    else:
        gross_margin_percent = _percent(
            (gross_profit / net_revenue) * Decimal("100")
        )

    return SalesGrossProfitabilityProjection(
        net_revenue=net_revenue,
        cogs=cogs,
        gross_profit=gross_profit,
        gross_margin_percent=gross_margin_percent,
    )
