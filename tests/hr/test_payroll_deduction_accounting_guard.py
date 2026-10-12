from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.services.payroll_deduction_result_service import (
    PayrollDeductionResultConflictError,
    require_authorized_deduction_result,
)


def test_unauthorized_positive_deduction_is_blocked():
    result = SimpleNamespace(
        deduction_amount=Decimal("654.00")
    )

    with pytest.raises(
        PayrollDeductionResultConflictError,
        match="not authorized",
    ):
        require_authorized_deduction_result(result)


@pytest.mark.parametrize(
    "amount",
    [
        Decimal("-1.00"),
        Decimal("NaN"),
        Decimal("Infinity"),
        "invalid",
        None,
    ],
)
def test_invalid_deduction_amount_is_blocked(amount):
    result = SimpleNamespace(deduction_amount=amount)

    with pytest.raises(PayrollDeductionResultConflictError):
        require_authorized_deduction_result(result)


def test_zero_deduction_is_allowed():
    result = SimpleNamespace(
        deduction_amount=Decimal("0.00")
    )

    require_authorized_deduction_result(result)
