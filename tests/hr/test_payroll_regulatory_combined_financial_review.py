from copy import deepcopy
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.services.payroll_regulatory_combined_financial_review import (
    CombinedFinancialReviewError,
    amount,
    canonical_digest,
    positive_id,
    review_financial_groups,
    validate_combined_review,
)


def group():
    return {
        "status": "REVIEW_ONLY_NOT_FOR_FILING",
        "statutory_approved": False,
        "company_id": 1,
        "payroll_period_id": 2,
        "employee_id": 3,
        "reviewed_category_code": "INTERNAL_REVIEW",
        "income_month": "2026-10-01",
        "source_fingerprint": "a" * 64,
        "payroll_calculation_ids": [10],
        "gross_accrued": "1000.00",
        "pit_accrued": "180.00",
        "levy_accrued": "50.00",
        "paid_amount": "0.00",
        "pit_remitted": "0.00",
        "levy_remitted": "0.00",
    }


def totals():
    return SimpleNamespace(
        gross=Decimal("1000.00"),
        pit=Decimal("180.00"),
        military_levy=Decimal("50.00"),
    )


def run(groups):
    return review_financial_groups(
        company_id=1,
        payroll_period_id=2,
        calculation_sources=[
            {"payroll_calculation_id": 10, "employee_id": 3}
        ],
        totals=totals(),
        groups=groups,
    )


def test_deterministic_digest():
    assert canonical_digest({"x": 1, "y": 2}) == (
        canonical_digest({"y": 2, "x": 1})
    )


def test_invalid_ids():
    for value in (0, -1, True, None, "5"):
        with pytest.raises(CombinedFinancialReviewError):
            positive_id(value, "id")


def test_financial_amount_rejects_floats():
    with pytest.raises(CombinedFinancialReviewError):
        amount(10.2, "amount")


def test_no_4df_groups_remains_unverified():
    assert run(None)["status"] == "NOT_VERIFIED"


def test_valid_accruals_remain_review_only():
    result = run([group()])
    assert result["gross_accrued"] == "1000.00"
    assert result["pit_accrued"] == "180.00"
    assert result["levy_accrued"] == "50.00"
    assert result["gross_paid_income_verified"] is False
    assert result["official_generation"] == "BLOCKED"
    assert result["status"].endswith(
        "SOURCE_BINDING_PENDING"
    )


def test_gross_mismatch_blocked():
    changed = group()
    changed["gross_accrued"] = "999.00"

    with pytest.raises(
        CombinedFinancialReviewError,
        match="differ",
    ):
        run([changed])


def test_pit_mismatch_blocked():
    changed = group()
    changed["pit_accrued"] = "179.00"

    with pytest.raises(CombinedFinancialReviewError):
        run([changed])


def test_wrong_employee_blocked():
    changed = group()
    changed["employee_id"] = 999

    with pytest.raises(
        CombinedFinancialReviewError,
        match="employee/payroll",
    ):
        run([changed])


def test_statutory_attribution_blocked():
    changed = group()
    changed["gross_paid_income_verified"] = True

    with pytest.raises(CombinedFinancialReviewError):
        run([changed])


def test_empty_financial_groups_rejected():
    with pytest.raises(CombinedFinancialReviewError):
        run([])


def test_invalid_combined_envelope_rejected():
    with pytest.raises(CombinedFinancialReviewError):
        validate_combined_review({"official_generation": "ENABLED"})


def test_group_inputs_not_modified():
    original = group()
    before = deepcopy(original)
    run([original])
    assert original == before
