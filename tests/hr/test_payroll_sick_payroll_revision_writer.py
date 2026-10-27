"""Validation contracts for immutable payroll revision line sets."""
from decimal import Decimal as D

import pytest

from app.services.payroll_sick_payroll_revision_writer import (
    validate_revision_lines,
)
from app.services.payroll_sick_payroll_replay import (
    SickPayrollReplayError,
)


def line(number, amount, sick=True):
    return {
        "line_no": number,
        "line_type": "sick_pay" if sick else "salary",
        "source_sick_leave_calculation_id": (
            20 if sick else None
        ),
        "currency_code": "UAH",
        "amount": D(amount),
        "quantity": D("1"),
        "rate": D(amount),
    }


def test_valid_frozen_lines():
    rows = [
        line(1, "1000", False),
        line(2, "250", True),
    ]
    result = validate_revision_lines(
        rows, D("1250"), "UAH"
    )
    assert len(result) == 2


def test_wrong_gross_refused():
    with pytest.raises(
        SickPayrollReplayError,
        match="do not reconcile",
    ):
        validate_revision_lines(
            [line(1, "250")],
            D("300"),
            "UAH",
        )


def test_duplicate_number_refused():
    with pytest.raises(
        SickPayrollReplayError,
        match="Duplicate",
    ):
        validate_revision_lines(
            [
                line(1, "250"),
                line(1, "300"),
            ],
            D("550"),
            "UAH",
        )


def test_wrong_currency_refused():
    with pytest.raises(
        SickPayrollReplayError,
        match="currency mismatch",
    ):
        validate_revision_lines(
            [line(1, "250")],
            D("250"),
            "EUR",
        )


def test_untrusted_extra_columns_refused():
    bad = line(1, "250")
    bad["payroll_calculation_id"] = 999

    with pytest.raises(
        SickPayrollReplayError,
        match="Unsupported",
    ):
        validate_revision_lines(
            [bad],
            D("250"),
            "UAH",
        )
