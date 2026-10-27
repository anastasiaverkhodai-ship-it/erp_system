"""Contracts for linked sick-payroll financial reconciliation."""
from decimal import Decimal as D
from types import SimpleNamespace as NS

import pytest

from app.services.payroll_sick_correction_plan import (
    PayrollFinancialSnapshot,
)
from app.services.payroll_sick_financial_replay_bridge import (
    reconcile_sick_payroll_replay,
    SickFinancialReplayError,
)


def snapshot(gross, tax, levy, net):
    return PayrollFinancialSnapshot(
        gross=D(gross),
        employer_gross=D(gross),
        insurer_gross=D("0.00"),
        personal_income_tax=D(tax),
        military_levy=D(levy),
        employer_contribution=D("0.00"),
        net=D(net),
    )


def test_positive_reconciliation():
    original = snapshot("1000.00", "180.00", "50.00", "770.00")
    replacement = snapshot("1500.00", "270.00", "75.00", "1155.00")

    replay = NS(
        original_gross=D("1000.00"),
        replacement_gross=D("1500.00"),
        gross_delta=D("500.00"),
    )

    result = reconcile_sick_payroll_replay(
        replay=replay,
        original_financial=original,
        replacement_financial=replacement,
        employee_settled=D("770.00"),
        insurer_received=D("0.00"),
    )

    assert result.gross_delta == D("500.00")
    assert result.net_delta == D("385.00")
    assert result.employee_due == D("385.00")
    assert result.employee_overpayment == D("0.00")
    assert sum(
        (row[1] for row in result.journal_lines), D(0)
    ) == sum(
        (row[2] for row in result.journal_lines), D(0)
    )


def test_replay_gross_mismatch_refused():
    original = snapshot("1000.00", "180.00", "50.00", "770.00")
    replacement = snapshot("1500.00", "270.00", "75.00", "1155.00")

    replay = NS(
        original_gross=D("1000.00"),
        replacement_gross=D("1400.00"),
        gross_delta=D("400.00"),
    )

    with pytest.raises(
        SickFinancialReplayError,
        match="do not reconcile",
    ):
        reconcile_sick_payroll_replay(
            replay=replay,
            original_financial=original,
            replacement_financial=replacement,
            employee_settled=D("770.00"),
            insurer_received=D("0.00"),
        )


def test_overpayment_is_not_silently_recovered():
    original = snapshot("1500.00", "270.00", "75.00", "1155.00")
    replacement = snapshot("1000.00", "180.00", "50.00", "770.00")

    replay = NS(
        original_gross=D("1500.00"),
        replacement_gross=D("1000.00"),
        gross_delta=D("-500.00"),
    )

    result = reconcile_sick_payroll_replay(
        replay=replay,
        original_financial=original,
        replacement_financial=replacement,
        employee_settled=D("1155.00"),
        insurer_received=D("0.00"),
    )

    assert result.employee_due == D("0.00")
    assert result.employee_overpayment == D("385.00")


def test_invalid_financial_snapshot_refused():
    original = snapshot("1000.00", "180.00", "50.00", "770.00")
    invalid = snapshot("1500.00", "270.00", "75.00", "1200.00")

    replay = NS(
        original_gross=D("1000.00"),
        replacement_gross=D("1500.00"),
        gross_delta=D("500.00"),
    )

    with pytest.raises(SickFinancialReplayError):
        reconcile_sick_payroll_replay(
            replay=replay,
            original_financial=original,
            replacement_financial=invalid,
            employee_settled=D("770.00"),
            insurer_received=D("0.00"),
        )
