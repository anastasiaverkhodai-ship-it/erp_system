from decimal import Decimal

import pytest

from app.services.payroll_cross_calculation_posting import (
    CrossCalculationPostingError,
    validate_posting_items,
)


def test_two_valid_calculations():
    assert validate_posting_items([
        {"payroll_calculation_id": 1, "amount": "100.00"},
        {"payroll_calculation_id": 2, "amount": "50.00"},
    ]) == [
        (1, Decimal("100.00")),
        (2, Decimal("50.00")),
    ]


def test_one_calculation_is_not_cross_calculation():
    with pytest.raises(CrossCalculationPostingError):
        validate_posting_items([
            {"payroll_calculation_id": 1, "amount": "10.00"},
        ])


def test_duplicate_calculation_rejected():
    with pytest.raises(CrossCalculationPostingError):
        validate_posting_items([
            {"payroll_calculation_id": 1, "amount": "10.00"},
            {"payroll_calculation_id": 1, "amount": "20.00"},
        ])


@pytest.mark.parametrize(
    "bad",
    ["-1.00", "0.00", "NaN", "Infinity", "1.001", 1.0],
)
def test_invalid_money_rejected(bad):
    with pytest.raises(CrossCalculationPostingError):
        validate_posting_items([
            {"payroll_calculation_id": 1, "amount": "10.00"},
            {"payroll_calculation_id": 2, "amount": bad},
        ])


@pytest.mark.parametrize("bad", [0, -1, True, "2"])
def test_invalid_calculation_identity(bad):
    with pytest.raises(CrossCalculationPostingError):
        validate_posting_items([
            {"payroll_calculation_id": 1, "amount": "10.00"},
            {"payroll_calculation_id": bad, "amount": "20.00"},
        ])


def test_float_amount_not_accepted():
    with pytest.raises(CrossCalculationPostingError):
        validate_posting_items([
            {"payroll_calculation_id": 1, "amount": 10.5},
            {"payroll_calculation_id": 2, "amount": "20.00"},
        ])
