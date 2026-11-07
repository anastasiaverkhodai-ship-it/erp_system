from decimal import Decimal

import pytest

from app.services.payroll_payment_tax_allocation_candidate import (
    PaymentTaxAllocationError,
)
from app.services.payroll_payment_tax_independent_source_audit import (
    classify_sources,
)


def classify(**overrides):
    args = {
        "payroll_posted": True,
        "payroll_confirmed": True,
        "bank_matches": 1,
        "cash_documents": 0,
        "tax_remittance_components": {
            "personal_income_tax": Decimal("180.00"),
            "military_levy": Decimal("50.00"),
        },
        "independent_review_count": 1,
    }
    args.update(overrides)
    return classify_sources(**args)


def test_bank_and_tax_sources_do_not_prove_withholding():
    result = classify()
    assert (
        result["payroll_source_status"]
        == "BANK_RECONCILIATION_PRESENT"
    )
    assert result["payment_specific_withholding_verified"] is False
    assert result["official_generation"] == "BLOCKED"


def test_no_payment_confirmation_fails_closed():
    result = classify(payroll_confirmed=False)
    assert (
        result["payroll_source_status"]
        == "NOT_CONFIRMED_AND_POSTED"
    )
    assert result["gross_paid_attribution"] == "NOT_APPROVED"


def test_conflicting_cash_and_bank_sources():
    result = classify(cash_documents=1)
    assert (
        result["payroll_source_status"]
        == "CONFLICTING_BANK_AND_CASH_SOURCES"
    )
    assert result["payment_specific_pit_withheld"] == "NOT_VERIFIED"


@pytest.mark.parametrize(
    "override",
    [
        {"bank_matches": -1},
        {"payroll_posted": 1},
        {
            "tax_remittance_components": {
                "personal_income_tax": Decimal("NaN"),
                "military_levy": Decimal("0.00"),
            }
        },
        {
            "tax_remittance_components": {
                "personal_income_tax": Decimal("1.001"),
                "military_levy": Decimal("0.00"),
            }
        },
    ],
)
def test_invalid_source_inputs(override):
    with pytest.raises(PaymentTaxAllocationError):
        classify(**override)
