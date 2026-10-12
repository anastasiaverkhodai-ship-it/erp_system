from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.payroll_deduction_result_service import (
    PayrollDeductionResultConflictError,
    require_authorized_deduction_result,
)


@pytest.mark.parametrize(
    "amount",
    [
        Decimal("0.01"),
        Decimal("654.00"),
        Decimal("1000000.00"),
    ],
)
def test_unauthorized_positive_deduction_is_blocked(amount):
    result = SimpleNamespace(deduction_amount=amount)

    with pytest.raises(PayrollDeductionResultConflictError):
        require_authorized_deduction_result(result)


def test_zero_deduction_is_allowed():
    result = SimpleNamespace(deduction_amount=Decimal("0.00"))

    require_authorized_deduction_result(result)


@pytest.mark.parametrize(
    "amount",
    [
        Decimal("-1.00"),
        Decimal("NaN"),
        Decimal("Infinity"),
        None,
        "invalid",
    ],
)
def test_invalid_deduction_amount_is_blocked(amount):
    result = SimpleNamespace(deduction_amount=amount)

    with pytest.raises(PayrollDeductionResultConflictError):
        require_authorized_deduction_result(result)


def test_register_has_authorization_guard():
    source = Path(
        "app/services/payroll_register_service.py"
    ).read_text()

    guard = "require_authorized_deduction_result(deduction)"
    amount = "deduction_amount = deduction.deduction_amount"

    assert guard in source
    assert amount in source
    assert source.index(guard) < source.index(amount)


def test_employee_balance_has_fail_closed_contract():
    source = Path(
        "app/services/payroll_employee_balance_service.py"
    ).read_text()

    assert "require_authorized_deduction_result(source)" in source
    assert "issues.append('unauthorized_payroll_deduction')" in source
    assert "row['balance']=None if issues" in source
    assert "complete=not issues" in source
