"""Tests for the fail-closed payroll legal-rule approval contract."""

from dataclasses import replace
from datetime import date, datetime, timezone

import pytest

from app.services.payroll_deduction_rule_approval_contract import (
    PayrollDeductionRuleApprovalError,
    RuleApprovalRecord,
    RuleApprovalStatus,
    require_approved_rule_record,
    validate_rule_approval,
)


def record(status=RuleApprovalStatus.PENDING):
    return RuleApprovalRecord(
        category="enforcement_other",
        rule_version="draft-v1",
        legal_reference="Legal review required",
        valid_from=date(2026, 1, 1),
        valid_to=None,
        status=status,
        created_by=10,
    )


def approved_record():
    return replace(
        record(),
        status=RuleApprovalStatus.APPROVED,
        approved_by=20,
        approved_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def test_pending_record_is_structurally_valid_but_not_approved():
    pending = record()
    validate_rule_approval(pending)

    with pytest.raises(PayrollDeductionRuleApprovalError):
        require_approved_rule_record(
            pending,
            category="enforcement_other",
            effective_on=date(2026, 2, 1),
        )


def test_approved_record_passes_snapshot_validation():
    require_approved_rule_record(
        approved_record(),
        category="enforcement_other",
        effective_on=date(2026, 2, 1),
    )


def test_creator_cannot_approve_own_rule():
    invalid = replace(approved_record(), approved_by=10)

    with pytest.raises(
        PayrollDeductionRuleApprovalError,
        match="Creator cannot approve",
    ):
        validate_rule_approval(invalid)


def test_unverified_category_is_rejected():
    invalid = replace(record(), category="unknown")

    with pytest.raises(PayrollDeductionRuleApprovalError):
        validate_rule_approval(invalid)


def test_wrong_category_is_rejected():
    with pytest.raises(
        PayrollDeductionRuleApprovalError,
        match="category mismatch",
    ):
        require_approved_rule_record(
            approved_record(),
            category="minor_child_alimony",
            effective_on=date(2026, 2, 1),
        )


def test_expired_rule_is_rejected():
    expired = replace(
        approved_record(),
        valid_to=date(2026, 1, 31),
    )

    with pytest.raises(
        PayrollDeductionRuleApprovalError,
        match="expired",
    ):
        require_approved_rule_record(
            expired,
            category="enforcement_other",
            effective_on=date(2026, 2, 1),
        )


def test_revoked_rule_is_rejected():
    revoked = replace(
        approved_record(),
        status=RuleApprovalStatus.REVOKED,
        revoked_by=30,
        revoked_at=datetime(2026, 2, 1, tzinfo=timezone.utc),
    )

    validate_rule_approval(revoked)

    with pytest.raises(
        PayrollDeductionRuleApprovalError,
        match="not approved",
    ):
        require_approved_rule_record(
            revoked,
            category="enforcement_other",
            effective_on=date(2026, 2, 2),
        )


def test_naive_approval_timestamp_is_rejected():
    invalid = replace(
        approved_record(),
        approved_at=datetime(2026, 1, 1),
    )

    with pytest.raises(
        PayrollDeductionRuleApprovalError,
        match="Timezone-aware",
    ):
        validate_rule_approval(invalid)


def test_pending_cannot_contain_approval_metadata():
    invalid = replace(record(), approved_by=20)

    with pytest.raises(PayrollDeductionRuleApprovalError):
        validate_rule_approval(invalid)


def test_hardening_invalid_rule_version():
    invalid = replace(record(), rule_version="   ")

    with pytest.raises(
        PayrollDeductionRuleApprovalError,
        match="Rule version",
    ):
        validate_rule_approval(invalid)


def test_hardening_invalid_legal_reference():
    invalid = replace(record(), legal_reference="")

    with pytest.raises(
        PayrollDeductionRuleApprovalError,
        match="Legal reference",
    ):
        validate_rule_approval(invalid)


@pytest.mark.parametrize("creator", [0, -1, True, "10", None])
def test_hardening_invalid_creator(creator):
    invalid = replace(record(), created_by=creator)

    with pytest.raises(PayrollDeductionRuleApprovalError):
        validate_rule_approval(invalid)


def test_hardening_invalid_validity_period():
    invalid = replace(
        record(),
        valid_from=date(2026, 3, 1),
        valid_to=date(2026, 2, 1),
    )

    with pytest.raises(
        PayrollDeductionRuleApprovalError,
        match="validity period",
    ):
        validate_rule_approval(invalid)


def test_hardening_datetime_not_accepted_as_effective_date():
    with pytest.raises(
        PayrollDeductionRuleApprovalError,
        match="Effective date",
    ):
        require_approved_rule_record(
            approved_record(),
            category="enforcement_other",
            effective_on=datetime(2026, 2, 1, tzinfo=timezone.utc),
        )


def test_hardening_approval_before_validity_rejected_by_policy():
    invalid = replace(
        approved_record(),
        valid_from=date(2026, 3, 1),
    )

    with pytest.raises(
        PayrollDeductionRuleApprovalError,
        match="not yet effective",
    ):
        require_approved_rule_record(
            invalid,
            category="enforcement_other",
            effective_on=date(2026, 2, 1),
        )


def test_hardening_revocation_before_approval():
    invalid = replace(
        approved_record(),
        status=RuleApprovalStatus.REVOKED,
        revoked_by=30,
        revoked_at=datetime(2025, 12, 31, tzinfo=timezone.utc),
    )

    with pytest.raises(
        PayrollDeductionRuleApprovalError,
        match="Revocation cannot precede approval",
    ):
        validate_rule_approval(invalid)


@pytest.mark.parametrize("approver", [0, -1, True, "20", None])
def test_hardening_invalid_approver(approver):
    invalid = replace(approved_record(), approved_by=approver)

    with pytest.raises(PayrollDeductionRuleApprovalError):
        validate_rule_approval(invalid)


def test_hardening_revoked_record_cannot_reactivate():
    revoked = replace(
        approved_record(),
        status=RuleApprovalStatus.REVOKED,
        revoked_by=30,
        revoked_at=datetime(2026, 2, 1, tzinfo=timezone.utc),
    )

    with pytest.raises(
        PayrollDeductionRuleApprovalError,
        match="not approved",
    ):
        require_approved_rule_record(
            revoked,
            category="enforcement_other",
            effective_on=date(2026, 1, 15),
        )
