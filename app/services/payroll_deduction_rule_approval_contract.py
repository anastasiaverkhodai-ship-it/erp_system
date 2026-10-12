"""Fail-closed contract for payroll deduction legal-rule approvals.

This module validates approval records but does not:
- authenticate users;
- grant permissions;
- persist approvals;
- establish legal applicability;
- authorize payroll deductions.

A separate trusted persistence and authorization layer is required.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum


class PayrollDeductionRuleApprovalError(ValueError):
    """Invalid or unauthorized legal-rule approval state."""


class RuleApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REVOKED = "revoked"


@dataclass(frozen=True)
class RuleApprovalRecord:
    category: str
    rule_version: str
    legal_reference: str
    valid_from: date
    valid_to: date | None
    status: RuleApprovalStatus
    created_by: int
    approved_by: int | None = None
    approved_at: datetime | None = None
    revoked_by: int | None = None
    revoked_at: datetime | None = None
    company_id: int | None = None


def validate_rule_approval(record: RuleApprovalRecord) -> None:
    """Validate record structure without granting legal authority."""

    if not isinstance(record, RuleApprovalRecord):
        raise PayrollDeductionRuleApprovalError(
            "A rule approval record is required"
        )

    from app.services.payroll_deduction_legal_classification import (
        CATEGORIES,
    )

    if record.category not in CATEGORIES:
        raise PayrollDeductionRuleApprovalError(
            "Unknown legal deduction category"
        )

    if not isinstance(record.rule_version, str) or not record.rule_version.strip():
        raise PayrollDeductionRuleApprovalError(
            "Rule version is required"
        )

    if not isinstance(record.legal_reference, str) or not record.legal_reference.strip():
        raise PayrollDeductionRuleApprovalError(
            "Legal reference is required"
        )

    if type(record.valid_from) is not date:
        raise PayrollDeductionRuleApprovalError(
            "Valid-from date is required"
        )

    if record.valid_to is not None:
        if type(record.valid_to) is not date or record.valid_to < record.valid_from:
            raise PayrollDeductionRuleApprovalError(
                "Invalid rule validity period"
            )

    if type(record.created_by) is not int or record.created_by <= 0:
        raise PayrollDeductionRuleApprovalError(
            "Valid creator is required"
        )

    if not isinstance(record.status, RuleApprovalStatus):
        raise PayrollDeductionRuleApprovalError(
            "Invalid approval status"
        )

    if record.status is RuleApprovalStatus.PENDING:
        if any(
            value is not None
            for value in (
                record.approved_by,
                record.approved_at,
                record.revoked_by,
                record.revoked_at,
            )
        ):
            raise PayrollDeductionRuleApprovalError(
                "Pending rule cannot contain approval or revocation metadata"
            )
        return

    if type(record.approved_by) is not int or record.approved_by <= 0:
        raise PayrollDeductionRuleApprovalError(
            "Valid approver is required"
        )

    if record.approved_by == record.created_by:
        raise PayrollDeductionRuleApprovalError(
            "Creator cannot approve their own rule"
        )

    if (
        not isinstance(record.approved_at, datetime)
        or record.approved_at.tzinfo is None
        or record.approved_at.utcoffset() is None
    ):
        raise PayrollDeductionRuleApprovalError(
            "Timezone-aware approval timestamp is required"
        )

    if record.status is RuleApprovalStatus.APPROVED:
        if record.revoked_by is not None or record.revoked_at is not None:
            raise PayrollDeductionRuleApprovalError(
                "Approved rule cannot contain revocation metadata"
            )
        return

    if type(record.revoked_by) is not int or record.revoked_by <= 0:
        raise PayrollDeductionRuleApprovalError(
            "Valid revoker is required"
        )

    if (
        not isinstance(record.revoked_at, datetime)
        or record.revoked_at.tzinfo is None
        or record.revoked_at.utcoffset() is None
    ):
        raise PayrollDeductionRuleApprovalError(
            "Timezone-aware revocation timestamp is required"
        )

    if record.revoked_at < record.approved_at:
        raise PayrollDeductionRuleApprovalError(
            "Revocation cannot precede approval"
        )


def require_approved_rule_record(
    record: RuleApprovalRecord,
    *,
    category: str,
    effective_on: date,
) -> None:
    """Validate an approval snapshot; never authorize payroll writes."""

    validate_rule_approval(record)

    if type(effective_on) is not date:
        raise PayrollDeductionRuleApprovalError(
            "Effective date is required"
        )

    if record.category != category:
        raise PayrollDeductionRuleApprovalError(
            "Rule category mismatch"
        )

    if record.status is not RuleApprovalStatus.APPROVED:
        raise PayrollDeductionRuleApprovalError(
            "Legal rule is not approved"
        )

    if effective_on < record.valid_from:
        raise PayrollDeductionRuleApprovalError(
            "Rule is not yet effective"
        )

    if record.valid_to is not None and effective_on > record.valid_to:
        raise PayrollDeductionRuleApprovalError(
            "Rule has expired"
        )
