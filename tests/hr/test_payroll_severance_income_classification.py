"""Test-first contract for payroll income-source classification.

These tests do not authorize taxes, deductions, accounting or payments.
The existing severance financial guard must remain fail-closed.
"""

from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.services.payroll_severance_income_classification import (
    SeveranceIncomeClassificationError,
    split_payroll_income,
)


def line(kind, amount, *, source=None, currency="UAH"):
    return SimpleNamespace(
        line_type=kind,
        amount=Decimal(amount),
        currency_code=currency,
        source_termination_severance_id=source,
    )


def test_salary_only_preserves_existing_income():
    result = split_payroll_income(
        gross_amount=Decimal("10000.00"),
        currency_code="UAH",
        lines=[line("salary", "10000.00")],
    )

    assert result.ordinary_gross == Decimal("10000.00")
    assert result.severance_gross == Decimal("0.00")
    assert result.total_gross == Decimal("10000.00")
    assert result.severance_source_ids == ()


def test_mixed_salary_and_documented_severance():
    result = split_payroll_income(
        gross_amount=Decimal("15000.00"),
        currency_code="UAH",
        lines=[
            line("salary", "10000.00"),
            line("severance", "5000.00", source=41),
        ],
    )

    assert result.ordinary_gross == Decimal("10000.00")
    assert result.severance_gross == Decimal("5000.00")
    assert result.total_gross == Decimal("15000.00")
    assert result.severance_source_ids == (41,)


def test_severance_only_has_no_ordinary_gross():
    result = split_payroll_income(
        gross_amount=Decimal("4000.00"),
        currency_code="UAH",
        lines=[
            line("severance", "4000.00", source=42),
        ],
    )

    assert result.ordinary_gross == Decimal("0.00")
    assert result.severance_gross == Decimal("4000.00")


def test_gross_mismatch_fails_closed():
    with pytest.raises(SeveranceIncomeClassificationError):
        split_payroll_income(
            gross_amount=Decimal("9000.00"),
            currency_code="UAH",
            lines=[
                line("salary", "8000.00"),
                line("severance", "2000.00", source=41),
            ],
        )


def test_undocumented_severance_source_fails_closed():
    with pytest.raises(SeveranceIncomeClassificationError):
        split_payroll_income(
            gross_amount=Decimal("2000.00"),
            currency_code="UAH",
            lines=[line("severance", "2000.00")],
        )


def test_severance_currency_mismatch_fails_closed():
    with pytest.raises(SeveranceIncomeClassificationError):
        split_payroll_income(
            gross_amount=Decimal("2000.00"),
            currency_code="UAH",
            lines=[
                line(
                    "severance",
                    "2000.00",
                    source=41,
                    currency="EUR",
                ),
            ],
        )


# P13-B3-B2-A SIGNED ADJUSTMENT TESTS


def test_negative_manual_adjustment_preserves_signed_income():
    result = split_payroll_income(
        gross_amount=Decimal("9000.00"),
        currency_code="UAH",
        lines=[
            line("salary", "10000.00"),
            line("manual_adjustment", "-1000.00"),
        ],
    )

    assert result.ordinary_gross == Decimal("9000.00")
    assert result.severance_gross == Decimal("0.00")
    assert result.total_gross == Decimal("9000.00")


def test_negative_adjustment_with_severance_keeps_separate_bases():
    result = split_payroll_income(
        gross_amount=Decimal("14000.00"),
        currency_code="UAH",
        lines=[
            line("salary", "10000.00"),
            line("manual_adjustment", "-1000.00"),
            line("severance", "5000.00", source=41),
        ],
    )

    assert result.ordinary_gross == Decimal("9000.00")
    assert result.severance_gross == Decimal("5000.00")
    assert result.severance_source_ids == (41,)


def test_positive_manual_adjustment_is_allowed():
    result = split_payroll_income(
        gross_amount=Decimal("11000.00"),
        currency_code="UAH",
        lines=[
            line("salary", "10000.00"),
            line("manual_adjustment", "1000.00"),
        ],
    )

    assert result.ordinary_gross == Decimal("11000.00")


def test_negative_severance_is_rejected():
    with pytest.raises(SeveranceIncomeClassificationError):
        split_payroll_income(
            gross_amount=Decimal("9000.00"),
            currency_code="UAH",
            lines=[
                line("salary", "10000.00"),
                line("severance", "-1000.00", source=41),
            ],
        )


def test_negative_salary_is_rejected():
    with pytest.raises(SeveranceIncomeClassificationError):
        split_payroll_income(
            gross_amount=Decimal("9000.00"),
            currency_code="UAH",
            lines=[
                line("salary", "-1000.00"),
                line("manual_adjustment", "10000.00"),
            ],
        )


def test_negative_total_gross_is_rejected():
    with pytest.raises(SeveranceIncomeClassificationError):
        split_payroll_income(
            gross_amount=Decimal("-100.00"),
            currency_code="UAH",
            lines=[
                line("manual_adjustment", "-100.00"),
            ],
        )
