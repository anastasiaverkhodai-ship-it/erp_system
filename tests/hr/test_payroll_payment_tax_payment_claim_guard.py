from copy import deepcopy
from datetime import date

import pytest

from app.services.payroll_payment_tax_allocation_candidate import (
    PaymentTaxAllocationError,
)
from app.services.payroll_payment_tax_payment_claim_guard import (
    assess_payment_tax_claims,
)


def example():
    return dict(
        company_id=12,
        payments=[
            dict(
                id=100,
                payment_date=date(2026, 9, 15),
                active=True,
                currency_code="UAH",
            ),
            dict(
                id=101,
                payment_date=date(2026, 9, 30),
                active=True,
                currency_code="UAH",
            ),
        ],
        claims=[
            dict(
                company_id=12,
                payment_id=100,
                component="personal_income_tax",
                amount="180.00",
                source_id=200,
                source_date=date(2026, 9, 15),
                currency_code="UAH",
            ),
            dict(
                company_id=12,
                payment_id=101,
                component="military_levy",
                amount="50.00",
                source_id=201,
                source_date=date(2026, 10, 1),
                currency_code="UAH",
            ),
        ],
    )


def test_valid_claims_are_not_verified():
    result = assess_payment_tax_claims(**example())
    assert result["claim_count"] == 2
    assert result["official_generation"] == "BLOCKED"
    assert result["payment_specific_withholding"] == "NOT_VERIFIED"
    assert all(
        c["actual_withholding"] == "NOT_VERIFIED"
        for c in result["claims"]
    )


@pytest.mark.parametrize("change", [
    lambda x: x["claims"][0].update(company_id=13),
    lambda x: x["claims"][0].update(payment_id=999),
    lambda x: x["claims"][0].update(component="other"),
    lambda x: x["claims"][0].update(amount="-1.00"),
    lambda x: x["claims"][0].update(amount="1.001"),
    lambda x: x["claims"][0].update(amount=1.0),
    lambda x: x["claims"][0].update(currency_code="EUR"),
    lambda x: x["claims"][0].update(
        source_date=date(2026, 9, 14)
    ),
    lambda x: x["claims"][1].update(source_id=200),
    lambda x: x["claims"][1].update(
        payment_id=100,
        component="personal_income_tax",
    ),
    lambda x: x["payments"][0].update(active=False),
    lambda x: x["payments"].append(
        deepcopy(x["payments"][0])
    ),
])
def test_invalid_claims_fail_closed(change):
    data = example()
    change(data)

    with pytest.raises(PaymentTaxAllocationError):
        assess_payment_tax_claims(**data)


def test_empty_claims_do_not_approve():
    data = example()
    data["claims"] = []
    result = assess_payment_tax_claims(**data)
    assert result["claim_count"] == 0
    assert result["official_generation"] == "BLOCKED"
    assert result["payment_specific_withholding"] == "NOT_VERIFIED"


def test_invalid_company_identity_blocked():
    data = example()
    data["company_id"] = True

    with pytest.raises(PaymentTaxAllocationError):
        assess_payment_tax_claims(**data)
