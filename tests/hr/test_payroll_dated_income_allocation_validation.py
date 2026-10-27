"""Structural and invalid-input contracts for dated allocations."""

from dataclasses import replace
from datetime import date
from decimal import Decimal as D

import pytest

from app.services.payroll_dated_income_allocation_validation import (
    DatedIncomeAllocationError,
    DatedIncomeSlice,
    validate_dated_income_slices,
)


def sample():
    return (
        DatedIncomeSlice(
            source_id=101,
            contract_id=9,
            income_from=date(2026, 9, 1),
            income_to=date(2026, 9, 14),
            gross_amount=D("4000.00"),
            rate_id=201,
            rate_from=date(2026, 9, 1),
            rate_to=date(2026, 9, 14),
            rate=D("0.18"),
        ),
        DatedIncomeSlice(
            source_id=102,
            contract_id=9,
            income_from=date(2026, 9, 15),
            income_to=date(2026, 9, 30),
            gross_amount=D("6000.00"),
            rate_id=202,
            rate_from=date(2026, 9, 15),
            rate_to=date(2026, 9, 30),
            rate=D("0.19"),
        ),
    )


def validate(rows, gross=D("10000.00")):
    return validate_dated_income_slices(
        rows,
        contract_id=9,
        period_start=date(2026, 9, 1),
        period_end=date(2026, 9, 30),
        expected_gross=gross,
    )


def test_positive_two_dated_slices():
    rows = sample()
    assert validate(tuple(reversed(rows))) == rows


@pytest.mark.parametrize(
    "change",
    [
        lambda a, b: (a, replace(b, income_from=date(2026, 9, 14))),
        lambda a, b: (a, replace(b, income_from=date(2026, 9, 16))),
        lambda a, b: (a, replace(b, source_id=a.source_id)),
        lambda a, b: (a, replace(b, rate_id=0)),
        lambda a, b: (a, replace(b, contract_id=10)),
        lambda a, b: (a, replace(b, rate_from=date(2026, 9, 16))),
        lambda a, b: (a, replace(b, gross_amount=D("6000.001"))),
        lambda a, b: (a, replace(b, gross_amount=D("-1.00"))),
        lambda a, b: (a, replace(b, rate=D("1.01"))),
        lambda a, b: (a, replace(b, rate=D("NaN"))),
        lambda a, b: (a, replace(b, income_to=date(2026, 10, 1))),
        lambda a, b: (a, replace(b, source_id=-1)),
    ],
)
def test_rejects_invalid_allocation(change):
    with pytest.raises(DatedIncomeAllocationError):
        validate(change(*sample()))


def test_rejects_gross_mismatch():
    with pytest.raises(
        DatedIncomeAllocationError,
        match="reconcile",
    ):
        validate(sample(), D("9999.99"))


def test_rejects_missing_slices():
    with pytest.raises(DatedIncomeAllocationError):
        validate(())


def test_rejects_negative_expected_gross():
    with pytest.raises(DatedIncomeAllocationError):
        validate(sample(), D("-1.00"))


def test_rejects_noncent_expected_gross():
    with pytest.raises(DatedIncomeAllocationError):
        validate(sample(), D("10000.001"))


def test_rejects_missing_rate_end():
    a, b = sample()
    with pytest.raises(DatedIncomeAllocationError):
        validate((a, replace(b, rate_to=None)))
