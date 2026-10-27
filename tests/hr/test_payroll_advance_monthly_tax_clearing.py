from decimal import Decimal

import pytest

from app.services.payroll_advance_monthly_tax_clearing import (
    MonthlyTaxClearingError,
    reconcile_monthly_advance_taxes,
)


def values():
    return dict(
        monthly_gross="20000.00",
        monthly_net="15400.00",
        advance_gross="8000.00",
        advance_net="6160.00",
        advance_tax_calculated={
            "personal_income_tax": "1440.00",
            "military_levy": "400.00",
        },
        monthly_tax_liability={
            "personal_income_tax": "3600.00",
            "military_levy": "1000.00",
        },
        tax_actually_remitted={
            "personal_income_tax": "1440.00",
            "military_levy": "400.00",
        },
    )


def test_partial_remittance_and_final_balance():
    result = reconcile_monthly_advance_taxes(**values())
    assert result.final_net_available == Decimal("9240.00")
    assert result.tax_unremitted == {
        "personal_income_tax": Decimal("2160.00"),
        "military_levy": Decimal("600.00"),
    }
    assert result.source_only is True
    assert result.financial_execution_allowed is False


def test_no_remittance_is_not_treated_as_payment():
    case = values()
    case["tax_actually_remitted"] = {
        "personal_income_tax": "0.00",
        "military_levy": "0.00",
    }
    result = reconcile_monthly_advance_taxes(**case)
    assert result.tax_unremitted == result.monthly_tax_liability
    assert result.final_net_available == Decimal("9240.00")


def test_full_remittance():
    case = values()
    case["tax_actually_remitted"] = case["monthly_tax_liability"].copy()
    result = reconcile_monthly_advance_taxes(**case)
    assert all(x == 0 for x in result.tax_unremitted.values())


@pytest.mark.parametrize("change", (
    "double_advance",
    "wrong_monthly_net",
    "wrong_advance_net",
    "excess_pit_remittance",
    "excess_advance_tax",
    "negative_remittance",
    "fractional_cent",
    "missing_component",
    "nonfinite_amount",
))
def test_invalid_clearing_rejected(change):
    case = values()
    if change == "double_advance":
        case["advance_net"] = "16000.00"
        case["advance_gross"] = "17840.00"
    elif change == "wrong_monthly_net":
        case["monthly_net"] = "15300.00"
    elif change == "wrong_advance_net":
        case["advance_net"] = "6200.00"
    elif change == "excess_pit_remittance":
        case["tax_actually_remitted"]["personal_income_tax"] = "3600.01"
    elif change == "excess_advance_tax":
        case["advance_tax_calculated"]["military_levy"] = "1000.01"
    elif change == "negative_remittance":
        case["tax_actually_remitted"]["military_levy"] = "-1.00"
    elif change == "fractional_cent":
        case["monthly_gross"] = "20000.001"
    elif change == "missing_component":
        del case["monthly_tax_liability"]["military_levy"]
    elif change == "nonfinite_amount":
        case["monthly_gross"] = "NaN"

    with pytest.raises(MonthlyTaxClearingError):
        reconcile_monthly_advance_taxes(**case)
