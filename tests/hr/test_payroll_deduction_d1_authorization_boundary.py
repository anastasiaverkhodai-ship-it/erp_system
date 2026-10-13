"""Phase 13.6-D1: fail-closed payroll deduction authorization.

These tests protect the existing safety boundary.
They do not authorize legal withholding or payment execution.
"""

from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.services.payroll_deduction_activation_policy import (
    PayrollDeductionActivationRequirements,
    evaluate_payroll_deduction_activation,
    require_payroll_deduction_activation,
    PayrollDeductionActivationError,
)

from app.services.payroll_deduction_result_service import (
    PayrollDeductionResultConflictError,
    require_authorized_deduction_result,
)


def test_d1_default_activation_is_denied():
    requirements = PayrollDeductionActivationRequirements()

    decision = evaluate_payroll_deduction_activation(
        requirements
    )

    assert decision.allowed is False

    with pytest.raises(PayrollDeductionActivationError):
        require_payroll_deduction_activation(requirements)


@pytest.mark.parametrize(
    "missing_requirement",
    [
        "legal_rules_approved",
        "document_authenticity_confirmed",
        "priority_rules_approved",
        "remainder_handling_approved",
        "payroll_integration_enabled",
    ],
)
def test_d1_every_activation_requirement_is_mandatory(
    missing_requirement,
):
    values = {
        "legal_rules_approved": True,
        "document_authenticity_confirmed": True,
        "priority_rules_approved": True,
        "remainder_handling_approved": True,
        "payroll_integration_enabled": True,
    }

    values[missing_requirement] = False

    requirements = PayrollDeductionActivationRequirements(
        **values
    )

    decision = evaluate_payroll_deduction_activation(
        requirements
    )

    assert decision.allowed is False

    with pytest.raises(PayrollDeductionActivationError):
        require_payroll_deduction_activation(requirements)


def test_d1_declared_prerequisites_do_not_authorize_financial_execution():
    requirements = PayrollDeductionActivationRequirements(
        legal_rules_approved=True,
        document_authenticity_confirmed=True,
        priority_rules_approved=True,
        remainder_handling_approved=True,
        payroll_integration_enabled=True,
    )

    decision = evaluate_payroll_deduction_activation(
        requirements
    )

    # A true policy evaluation only acknowledges declared prerequisites.
    assert decision.allowed is True

    # It must not bypass the independent financial execution guard.
    positive_result = SimpleNamespace(
        deduction_amount=Decimal("100.00")
    )

    with pytest.raises(PayrollDeductionResultConflictError):
        require_authorized_deduction_result(
            positive_result
        )


@pytest.mark.parametrize(
    "amount",
    [
        Decimal("0.01"),
        Decimal("100.00"),
        Decimal("999999.99"),
    ],
)
def test_d1_positive_deduction_remains_blocked(amount):
    result = SimpleNamespace(
        deduction_amount=amount
    )

    with pytest.raises(PayrollDeductionResultConflictError):
        require_authorized_deduction_result(result)


@pytest.mark.parametrize(
    "amount",
    [
        Decimal("-0.01"),
        Decimal("NaN"),
        Decimal("Infinity"),
    ],
)
def test_d1_invalid_deduction_remains_blocked(amount):
    result = SimpleNamespace(
        deduction_amount=amount
    )

    with pytest.raises(PayrollDeductionResultConflictError):
        require_authorized_deduction_result(result)


def test_d1_zero_deduction_remains_allowed():
    result = SimpleNamespace(
        deduction_amount=Decimal("0.00")
    )

    require_authorized_deduction_result(result)


def test_d1_missing_result_remains_allowed():
    require_authorized_deduction_result(None)
