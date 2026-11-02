from dataclasses import FrozenInstanceError
from decimal import Decimal as D

import pytest

from app.services.payroll_severance_net_tax_settlement import (
    SeveranceFinancialSnapshot,
    SeveranceSettlementError,
    plan_severance_settlement,
)


def snapshot(gross, pit, levy, net, usc="0.00"):
    return SeveranceFinancialSnapshot(
        D(gross), D(pit), D(levy), D(net), D(usc)
    )


ORIGINAL = snapshot(
    "10000.00", "1800.00", "500.00", "7700.00"
)


def test_additional_payment_and_tax_difference():
    corrected = snapshot(
        "12000.00", "2160.00", "600.00", "9240.00"
    )
    result = plan_severance_settlement(
        original=ORIGINAL,
        corrected=corrected,
        employee_paid=D("7700.00"),
        personal_income_tax_remitted=D("1800.00"),
        military_levy_remitted=D("500.00"),
    )

    assert result.gross_delta == D("2000.00")
    assert result.personal_income_tax_delta == D("360.00")
    assert result.military_levy_delta == D("100.00")
    assert result.net_delta == D("1540.00")
    assert result.employee_due == D("1540.00")
    assert result.employee_overpayment == D("0.00")
    assert result.personal_income_tax_balance == D("360.00")
    assert result.military_levy_balance == D("100.00")
    assert result.journal_posting_authorized is False
    assert result.employee_payment_authorized is False


def test_overpayment_after_paid_severance():
    corrected = snapshot(
        "8000.00", "1440.00", "400.00", "6160.00"
    )
    result = plan_severance_settlement(
        original=ORIGINAL,
        corrected=corrected,
        employee_paid=D("7700.00"),
        personal_income_tax_remitted=D("1800.00"),
        military_levy_remitted=D("500.00"),
    )
    assert result.gross_delta == D("-2000.00")
    assert result.net_delta == D("-1540.00")
    assert result.employee_due == D("0.00")
    assert result.employee_overpayment == D("1540.00")
    assert result.personal_income_tax_balance == D("-360.00")
    assert result.military_levy_balance == D("-100.00")
    assert result.employee_recovery_authorized is False
    assert result.tax_refund_authorized is False


def test_partial_confirmed_return_reduces_exposure():
    result = plan_severance_settlement(
        original=ORIGINAL,
        corrected=snapshot(
            "8000.00", "1440.00", "400.00", "6160.00"
        ),
        employee_paid="7700.00",
        employee_returned="540.00",
    )
    assert result.employee_overpayment == D("1000.00")


def test_no_change_fully_settled():
    result = plan_severance_settlement(
        original=ORIGINAL,
        corrected=ORIGINAL,
        employee_paid="7700.00",
        personal_income_tax_remitted="1800.00",
        military_levy_remitted="500.00",
    )
    assert result.employee_due == D("0.00")
    assert result.employee_overpayment == D("0.00")
    assert result.personal_income_tax_balance == D("0.00")


@pytest.mark.parametrize(
    "value",
    ["NaN", "Infinity", "3.001", "-1.00", True, "invalid"],
)
def test_bad_payment_rejected(value):
    with pytest.raises(SeveranceSettlementError):
        plan_severance_settlement(
            original=ORIGINAL,
            corrected=ORIGINAL,
            employee_paid=value,
        )


def test_unreconciled_snapshot_rejected():
    with pytest.raises(SeveranceSettlementError):
        plan_severance_settlement(
            original=ORIGINAL,
            corrected=snapshot(
                "10000.00", "1800.00",
                "500.00", "7800.00",
            ),
            employee_paid="7700.00",
        )


def test_unconfirmed_excess_return_rejected():
    with pytest.raises(SeveranceSettlementError):
        plan_severance_settlement(
            original=ORIGINAL,
            corrected=ORIGINAL,
            employee_paid="100.00",
            employee_returned="101.00",
        )


def test_immutable_result_and_fail_closed_permissions():
    plan = plan_severance_settlement(
        original=ORIGINAL,
        corrected=ORIGINAL,
        employee_paid="7700.00",
    )
    with pytest.raises(FrozenInstanceError):
        plan.employee_due = D("100.00")

    assert not plan.employee_payment_authorized
    assert not plan.employee_recovery_authorized
    assert not plan.tax_refund_authorized
    assert not plan.journal_posting_authorized
