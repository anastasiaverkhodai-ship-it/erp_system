import pytest
from decimal import Decimal

from app.services.payroll_payment_tax_allocation_candidate import (
    PaymentTaxAllocationError,
    money,
    require_id,
    require_text,
)


@pytest.mark.parametrize("value", [
    "0.00", "-1.00", "1.001", "NaN", "Infinity",
    1.0, True, None,
])
def test_invalid_money_rejected(value):
    with pytest.raises(PaymentTaxAllocationError):
        money(value)


def test_money_exact():
    assert money("12.34") == Decimal("12.34")


@pytest.mark.parametrize("value", [0, -1, True, "1", None])
def test_invalid_identity(value):
    with pytest.raises(PaymentTaxAllocationError):
        require_id(value, "source")


def test_reference_required():
    with pytest.raises(PaymentTaxAllocationError):
        require_text("", "evidence", 500)


def test_reference_no_whitespace():
    with pytest.raises(PaymentTaxAllocationError):
        require_text(" evidence ", "evidence", 500)


def test_component_scope_is_only_employee_tax():
    from app.services.payroll_payment_tax_allocation_candidate import COMPONENTS
    assert COMPONENTS == {
        "personal_income_tax", "military_levy"
    }
    assert "unified_social_contribution" not in COMPONENTS
