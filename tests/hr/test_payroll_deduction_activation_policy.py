"""Tests for the fail-closed activation policy."""

from dataclasses import replace

import pytest

from app.services.payroll_deduction_activation_policy import (
    PayrollDeductionActivationError,
    PayrollDeductionActivationRequirements,
    evaluate_payroll_deduction_activation,
    require_payroll_deduction_activation,
)


FIELDS = (
    "legal_rules_approved",
    "document_authenticity_confirmed",
    "priority_rules_approved",
    "remainder_handling_approved",
    "payroll_integration_enabled",
)


def approved_requirements():
    return PayrollDeductionActivationRequirements(
        legal_rules_approved=True,
        document_authenticity_confirmed=True,
        priority_rules_approved=True,
        remainder_handling_approved=True,
        payroll_integration_enabled=True,
    )


def test_default_is_disabled():
    decision = evaluate_payroll_deduction_activation(
        PayrollDeductionActivationRequirements()
    )

    assert decision.allowed is False


@pytest.mark.parametrize("field", FIELDS)
def test_each_missing_requirement_blocks_activation(field):
    requirements = replace(
        approved_requirements(),
        **{field: False},
    )

    decision = evaluate_payroll_deduction_activation(requirements)

    assert decision.allowed is False


@pytest.mark.parametrize("field", FIELDS)
def test_none_does_not_count_as_approval(field):
    requirements = replace(
        approved_requirements(),
        **{field: None},
    )

    decision = evaluate_payroll_deduction_activation(requirements)

    assert decision.allowed is False


@pytest.mark.parametrize("field", FIELDS)
def test_truthy_values_do_not_count_as_approval(field):
    requirements = replace(
        approved_requirements(),
        **{field: 1},
    )

    decision = evaluate_payroll_deduction_activation(requirements)

    assert decision.allowed is False


def test_invalid_requirements_fail_closed():
    decision = evaluate_payroll_deduction_activation(None)

    assert decision.allowed is False


def test_declared_prerequisites_can_be_evaluated():
    decision = evaluate_payroll_deduction_activation(
        approved_requirements()
    )

    assert decision.allowed is True


def test_require_rejects_disabled_integration():
    requirements = replace(
        approved_requirements(),
        payroll_integration_enabled=False,
    )

    with pytest.raises(
        PayrollDeductionActivationError,
        match="disabled",
    ):
        require_payroll_deduction_activation(requirements)


def test_require_accepts_declared_prerequisites():
    require_payroll_deduction_activation(
        approved_requirements()
    )
