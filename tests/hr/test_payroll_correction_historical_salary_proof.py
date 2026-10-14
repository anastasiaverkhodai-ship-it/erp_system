"""Historical salary-rate proof contract tests."""

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.services.payroll_correction_historical_salary_proof import (
    PayrollHistoricalSalaryProofError,
    verify_salary_rate_slice,
)


def _fixtures():
    rate = SimpleNamespace(
        id=10,
        company_id=1,
        employment_contract_id=2,
        rate_type="hourly",
        amount=Decimal("100.00"),
        currency_code="UAH",
        effective_from=date(2096, 4, 1),
        effective_to=date(2096, 4, 30),
    )

    salary_slice = SimpleNamespace(
        company_id=1,
        salary_rate_id=10,
        salary_rate_type="hourly",
        salary_rate_amount=Decimal("100.00"),
        currency_code="UAH",
        effective_from=date(2096, 4, 1),
        effective_to=date(2096, 4, 30),
    )

    return rate, salary_slice


def _verify(rate, salary_slice):
    verify_salary_rate_slice(
        rate,
        salary_slice,
        company_id=1,
        employment_contract_id=2,
    )


def test_matching_historical_rate_passes():
    rate, salary_slice = _fixtures()
    _verify(rate, salary_slice)


def test_missing_rate_rejected():
    _, salary_slice = _fixtures()

    with pytest.raises(
        PayrollHistoricalSalaryProofError,
        match="missing",
    ):
        _verify(None, salary_slice)


def test_wrong_employment_contract_rejected():
    rate, salary_slice = _fixtures()
    rate.employment_contract_id = 999

    with pytest.raises(
        PayrollHistoricalSalaryProofError,
        match="ownership",
    ):
        _verify(rate, salary_slice)


def test_rate_amount_mismatch_rejected():
    rate, salary_slice = _fixtures()
    rate.amount = Decimal("120.00")

    with pytest.raises(
        PayrollHistoricalSalaryProofError,
        match="terms",
    ):
        _verify(rate, salary_slice)


def test_currency_mismatch_rejected():
    rate, salary_slice = _fixtures()
    salary_slice.currency_code = "EUR"

    with pytest.raises(
        PayrollHistoricalSalaryProofError,
        match="terms",
    ):
        _verify(rate, salary_slice)


def test_rate_type_mismatch_rejected():
    rate, salary_slice = _fixtures()
    salary_slice.salary_rate_type = "monthly"

    with pytest.raises(
        PayrollHistoricalSalaryProofError,
        match="terms",
    ):
        _verify(rate, salary_slice)


def test_slice_outside_rate_dates_rejected():
    rate, salary_slice = _fixtures()
    salary_slice.effective_to = date(2096, 5, 1)

    with pytest.raises(
        PayrollHistoricalSalaryProofError,
        match="beyond",
    ):
        _verify(rate, salary_slice)


def test_nonfinite_rate_rejected():
    rate, salary_slice = _fixtures()
    rate.amount = Decimal("NaN")

    with pytest.raises(
        PayrollHistoricalSalaryProofError,
        match="Non-finite",
    ):
        _verify(rate, salary_slice)
