"""C2 approval-gated deduction planning integration.

This is a NON-POSTING technical planning boundary.

It requires an approved rule record for each policy line,
but it does not establish legal entitlement, bind a claim to
a verified enforcement document, or enable actual withholding.

Actual payroll settlement and journal posting must remain
under their existing independent authorization controls.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.services.payroll_deduction_aggregation_policy import (
    DeductionPolicyLine,
    DeductionPolicyPreview,
    preview_deduction_policy,
)
from app.services.payroll_deduction_rule_approval_contract import (
    RuleApprovalRecord,
    require_approved_rule_record,
)


class DeductionIntegrationError(ValueError):
    """Deduction integration contract rejected the request."""


@dataclass(frozen=True)
class ApprovedDeductionLine:
    policy_line: DeductionPolicyLine
    approval: RuleApprovalRecord


@dataclass(frozen=True)
class DeductionIntegrationPreview:
    company_id: int
    effective_on: date
    policy_preview: DeductionPolicyPreview
    payroll_posting_allowed: bool
    legal_authorization: str


def preview_approved_deduction_plan(
    *,
    company_id: int,
    effective_on: date,
    available_amount: Decimal,
    aggregate_limit: Decimal,
    lines: tuple[ApprovedDeductionLine, ...],
) -> DeductionIntegrationPreview:
    """Check rule approval and calculate a non-posting preview.

    A trusted application layer must resolve real approval rows and
    enforce the relationship between each deduction instruction
    and its legal documentation before production use.
    """

    if type(company_id) is not int or company_id <= 0:
        raise DeductionIntegrationError(
            "A positive company_id is required"
        )

    if type(effective_on) is not date:
        raise DeductionIntegrationError(
            "A valid effective_on date is required"
        )

    if type(lines) is not tuple:
        raise DeductionIntegrationError(
            "lines must be a tuple"
        )

    validated_lines = []

    for entry in lines:
        if not isinstance(entry, ApprovedDeductionLine):
            raise DeductionIntegrationError(
                "Invalid approved deduction line"
            )

        if not isinstance(entry.policy_line, DeductionPolicyLine):
            raise DeductionIntegrationError(
                "Invalid policy line"
            )

        if not isinstance(entry.approval, RuleApprovalRecord):
            raise DeductionIntegrationError(
                "A real RuleApprovalRecord is required"
            )

        if entry.approval.company_id != company_id:
            raise DeductionIntegrationError(
                "Approval belongs to another company"
            )

        # This function must reject pending, revoked, expired,
        # future-dated and category-mismatched approval records.
        require_approved_rule_record(
            entry.approval,
            category=entry.policy_line.legal_category,
            effective_on=effective_on,
        )

        validated_lines.append(entry.policy_line)

    policy_preview = preview_deduction_policy(
        available_amount=available_amount,
        aggregate_limit=aggregate_limit,
        lines=tuple(validated_lines),
    )

    if policy_preview.payroll_posting_allowed:
        raise DeductionIntegrationError(
            "Unsafe downstream posting authorization"
        )

    return DeductionIntegrationPreview(
        company_id=company_id,
        effective_on=effective_on,
        policy_preview=policy_preview,
        payroll_posting_allowed=False,
        legal_authorization="NOT_AUTHORIZED",
    )
