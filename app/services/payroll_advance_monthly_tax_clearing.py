"""Read-only monthly reconciliation for verified advance tax sources.

This module does not post money, allocate a bank remittance, or
authorize release of final pay. Actual remittances must be supplied
by an independently verified financial source.
"""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

CENT = Decimal("0.01")
EMPLOYEE_TAXES = ("personal_income_tax", "military_levy")


class MonthlyTaxClearingError(ValueError):
    pass


def money(value):
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise MonthlyTaxClearingError("Invalid amount") from exc
    if not amount.is_finite() or amount != amount.quantize(CENT):
        raise MonthlyTaxClearingError("Invalid monetary precision")
    if amount < 0:
        raise MonthlyTaxClearingError("Negative monetary amount")
    return amount


def components(values):
    if not isinstance(values, dict) or set(values) != set(EMPLOYEE_TAXES):
        raise MonthlyTaxClearingError(
            "Exactly PIT and military levy are required"
        )
    return {name: money(values[name]) for name in EMPLOYEE_TAXES}


@dataclass(frozen=True)
class MonthlyTaxClearing:
    monthly_gross: Decimal
    monthly_withholding: Decimal
    monthly_net: Decimal
    advance_net_paid: Decimal
    final_net_available: Decimal
    advance_tax_calculated: dict
    monthly_tax_liability: dict
    tax_actually_remitted: dict
    tax_unremitted: dict
    source_only: bool = True
    financial_execution_allowed: bool = False


def reconcile_monthly_advance_taxes(
    *,
    monthly_gross,
    monthly_net,
    advance_gross,
    advance_net,
    advance_tax_calculated,
    monthly_tax_liability,
    tax_actually_remitted,
):
    gross = money(monthly_gross)
    net = money(monthly_net)
    adv_gross = money(advance_gross)
    adv_net = money(advance_net)

    advance = components(advance_tax_calculated)
    liability = components(monthly_tax_liability)
    remitted = components(tax_actually_remitted)

    monthly_withholding = sum(liability.values(), Decimal("0"))
    advance_withholding = sum(advance.values(), Decimal("0"))

    if gross - monthly_withholding != net:
        raise MonthlyTaxClearingError(
            "Monthly net does not reconcile to employee taxes"
        )

    if adv_gross - advance_withholding != adv_net:
        raise MonthlyTaxClearingError(
            "Advance gross and tax source do not reconcile"
        )

    if adv_net > net:
        raise MonthlyTaxClearingError(
            "Advance exceeds final monthly net"
        )

    for component in EMPLOYEE_TAXES:
        if advance[component] > liability[component]:
            raise MonthlyTaxClearingError(
                "Advance component exceeds monthly tax liability"
            )
        if remitted[component] > liability[component]:
            raise MonthlyTaxClearingError(
                "Remittance exceeds monthly tax liability"
            )

    return MonthlyTaxClearing(
        monthly_gross=gross,
        monthly_withholding=monthly_withholding,
        monthly_net=net,
        advance_net_paid=adv_net,
        final_net_available=net - adv_net,
        advance_tax_calculated=advance,
        monthly_tax_liability=liability,
        tax_actually_remitted=remitted,
        tax_unremitted={
            component: liability[component] - remitted[component]
            for component in EMPLOYEE_TAXES
        },
    )
