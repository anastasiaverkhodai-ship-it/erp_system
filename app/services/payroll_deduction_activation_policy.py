"""Fail-closed authorization boundary for payroll legal deductions.

This module does not authenticate documents, grant approvals, or
activate payroll writes. Approval must come from trusted application
state, not caller-supplied flags.
"""

from __future__ import annotations

from dataclasses import dataclass


class PayrollDeductionActivationError(ValueError):
    """Legal deduction activation is not authorized."""


@dataclass(frozen=True)
class PayrollDeductionActivationDecision:
    allowed: bool
    reason: str


@dataclass(frozen=True)
class PayrollDeductionActivationRequirements:
    legal_rules_approved: bool = False
    document_authenticity_confirmed: bool = False
    priority_rules_approved: bool = False
    remainder_handling_approved: bool = False
    payroll_integration_enabled: bool = False


def evaluate_payroll_deduction_activation(
    requirements: PayrollDeductionActivationRequirements,
) -> PayrollDeductionActivationDecision:
    """Evaluate declared prerequisites; never independently authorize."""

    if not isinstance(
        requirements,
        PayrollDeductionActivationRequirements,
    ):
        return PayrollDeductionActivationDecision(
            allowed=False,
            reason="Invalid activation requirements",
        )

    checks = (
        (
            requirements.legal_rules_approved,
            "Legal rules have not been approved",
        ),
        (
            requirements.document_authenticity_confirmed,
            "Document authenticity has not been confirmed",
        ),
        (
            requirements.priority_rules_approved,
            "Deduction priority rules have not been approved",
        ),
        (
            requirements.remainder_handling_approved,
            "Unapplied deduction handling has not been approved",
        ),
        (
            requirements.payroll_integration_enabled,
            "Payroll integration is disabled",
        ),
    )

    for satisfied, reason in checks:
        if satisfied is not True:
            return PayrollDeductionActivationDecision(
                allowed=False,
                reason=reason,
            )

    return PayrollDeductionActivationDecision(
        allowed=True,
        reason="Declared activation prerequisites satisfied",
    )


def require_payroll_deduction_activation(
    requirements: PayrollDeductionActivationRequirements,
) -> None:
    """Reject requests missing any declared prerequisite."""

    decision = evaluate_payroll_deduction_activation(requirements)

    if not decision.allowed:
        raise PayrollDeductionActivationError(decision.reason)
