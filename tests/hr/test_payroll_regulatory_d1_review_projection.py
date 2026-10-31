from decimal import Decimal

import pytest

from app.services.payroll_regulatory_d1_review_projection import (
    D1ReviewProjectionError,
    checked_amount,
    make_d1_review_projection,
)


def test_d1_rejects_wrong_report_kind():
    with pytest.raises(D1ReviewProjectionError):
        make_d1_review_projection({
            "report_kind": "4df",
            "company_id": 1,
            "payroll_period_id": 1,
            "calculation_sources": [],
        })


def test_d1_rejects_empty_sources():
    with pytest.raises(
        D1ReviewProjectionError,
        match="sources required",
    ):
        make_d1_review_projection({
            "report_kind": "d1",
            "company_id": 1,
            "payroll_period_id": 1,
            "calculation_sources": [],
        })


@pytest.mark.parametrize(
    "value",
    ["NaN", "Infinity", "invalid", None, True, "1.001"],
)
def test_d1_rejects_invalid_money(value):
    with pytest.raises(D1ReviewProjectionError):
        checked_amount(value, "test")


def test_d1_preserves_valid_cents():
    assert checked_amount("123.45", "test") == Decimal("123.45")
