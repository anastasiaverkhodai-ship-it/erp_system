from decimal import Decimal as D
from types import SimpleNamespace

import pytest

from app.services.payroll_severance_adjustment_journal_service import (
    correction_journal_sides,
    SeveranceAdjustmentJournalError,
)


def delta(
    gross="100.00",
    pit="18.00",
    levy="5.00",
    usc="22.00",
    net="77.00",
):
    return SimpleNamespace(
        reconciled=True,
        gross_delta=D(gross),
        personal_income_tax_delta=D(pit),
        military_levy_delta=D(levy),
        employer_contribution_delta=D(usc),
        net_delta=D(net),
    )


def test_positive_correction_journal():
    rows = correction_journal_sides(delta())
    debit = sum((r[1] for r in rows), D("0"))
    credit = sum((r[2] for r in rows), D("0"))

    assert debit == credit == D("122.00")
    assert len(rows) == 6


def test_negative_correction_journal():
    rows = correction_journal_sides(
        delta("-100.00", "-18.00", "-5.00", "-22.00", "-77.00")
    )
    assert sum((r[1] for r in rows), D("0")) == D("122.00")
    assert sum((r[2] for r in rows), D("0")) == D("122.00")


def test_net_only_change():
    rows = correction_journal_sides(
        delta("100.00", "0.00", "0.00", "0.00", "100.00")
    )
    assert len(rows) == 2
    assert sum((r[1] for r in rows), D("0")) == D("100.00")
    assert sum((r[2] for r in rows), D("0")) == D("100.00")


def test_unbalanced_delta_rejected():
    with pytest.raises(
        SeveranceAdjustmentJournalError,
        match="Unbalanced",
    ):
        correction_journal_sides(
            delta("100.00", "18.00", "5.00", "22.00", "78.00")
        )


def test_unreconciled_delta_rejected():
    item = delta()
    item.reconciled = False

    with pytest.raises(SeveranceAdjustmentJournalError):
        correction_journal_sides(item)


def test_zero_correction_rejected():
    with pytest.raises(SeveranceAdjustmentJournalError):
        correction_journal_sides(
            delta("0.00", "0.00", "0.00", "0.00", "0.00")
        )
