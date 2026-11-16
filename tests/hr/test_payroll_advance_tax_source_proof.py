import json
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.services.payroll_advance_tax_source_proof import (
    AdvanceTaxSourceProofError,
    verify_automatic_advance_tax_source,
)


def fixture():
    advance = SimpleNamespace(
        id=10,
        company_id=2,
        payroll_period_id=5,
        employment_contract_id=7,
        bank_account_id=8,
        advance_percentage=Decimal("40"),
        payment_date=date(2026, 9, 16),
        currency_code="UAH",
        calculated_amount=Decimal("1000"),
        paid_amount=Decimal("770"),
        withheld_amount=Decimal("0"),
    )
    snapshot = {
        "version": 1,
        "request": {
            "company_id": 2,
            "payroll_period_id": 5,
            "employment_contract_id": 7,
            "bank_account_id": 8,
            "advance_percentage": "40.0000",
            "payment_date": "2026-09-16",
        },
        "gross_amount": "1000",
        "withholding_amount": "230",
        "net_amount": "770",
        "minimum_net_amount": "700",
        "tax_lines": [
            {
                "component": "personal_income_tax",
                "source_rate_id": 11,
                "source_tax_evidence_id": 21,
                "source_reference": "PIT source",
                "base_amount": "1000",
                "rate": "0.18",
                "amount": "180",
            },
            {
                "component": "military_levy",
                "source_rate_id": 12,
                "source_tax_evidence_id": 22,
                "source_reference": "VZ source",
                "base_amount": "1000",
                "rate": "0.05",
                "amount": "50",
            },
            {
                "component": "unified_social_contribution",
                "source_rate_id": 13,
                "source_tax_evidence_id": 23,
                "source_reference": "USC source",
                "base_amount": "1000",
                "rate": "0.22",
                "amount": "220",
            },
        ],
    }
    advance.calculation_snapshot_json = json.dumps(snapshot)
    return advance, snapshot


def test_exact_source_proof_does_not_authorize_clearing():
    advance, _ = fixture()
    proof = verify_automatic_advance_tax_source(advance)
    assert proof.advance_id == advance.id
    assert proof.withholding_amount == Decimal("230")
    assert proof.net_amount == Decimal("770")
    assert len(proof.components) == 3
    assert proof.tax_remittance_confirmed is False
    assert proof.monthly_tax_clearing_confirmed is False


@pytest.mark.parametrize("change", (
    "gross", "paid", "tax_amount", "tax_rate",
    "duplicate", "source", "request", "legacy",
))
def test_inconsistent_snapshot_is_blocked(change):
    advance, snapshot = fixture()
    if change == "gross":
        snapshot["gross_amount"] = "1001"
    elif change == "paid":
        advance.paid_amount = Decimal("771")
    elif change == "tax_amount":
        snapshot["tax_lines"][0]["amount"] = "181"
    elif change == "tax_rate":
        snapshot["tax_lines"][0]["rate"] = "0.17"
    elif change == "duplicate":
        snapshot["tax_lines"][1]["component"] = (
            "personal_income_tax"
        )
    elif change == "source":
        snapshot["tax_lines"][0]["source_rate_id"] = None
    elif change == "request":
        snapshot["request"]["bank_account_id"] = 99
    elif change == "legacy":
        advance.calculation_snapshot_json = None

    if change != "legacy":
        advance.calculation_snapshot_json = json.dumps(snapshot)

    with pytest.raises(AdvanceTaxSourceProofError):
        verify_automatic_advance_tax_source(advance)


def test_enforcement_deduction_preserves_advance_gross_and_tax_source():
    advance, _ = fixture()
    advance.paid_amount = Decimal('670')
    advance.withheld_amount = Decimal('100')
    proof = verify_automatic_advance_tax_source(advance)
    assert proof.gross_amount == Decimal('1000')
    assert proof.withholding_amount == Decimal('230')
    assert proof.net_amount == Decimal('770')


def test_non_tax_withholding_cannot_be_added_on_top_of_original_net():
    advance, _ = fixture()
    advance.withheld_amount = Decimal('100')
    with pytest.raises(AdvanceTaxSourceProofError):
        verify_automatic_advance_tax_source(advance)
