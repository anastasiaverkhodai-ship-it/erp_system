"""Resolve persisted payroll deduction approvals without activating payroll.

This service provides verified approval state, not financial authorization.
Automatic withholding remains blocked by existing execution guards.
"""

from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.payroll_deduction_rule_approval import (
    PayrollDeductionRuleApproval,
)
from app.services.payroll_deduction_rule_approval_contract import (
    PayrollDeductionRuleApprovalError,
    RuleApprovalRecord,
    RuleApprovalStatus,
    require_approved_rule_record,
)


class TrustedApprovalResolutionError(ValueError):
    """A unique, valid company-scoped approval was not established."""


async def resolve_trusted_rule_approval(
    db: AsyncSession,
    *,
    company_id: int,
    legal_category: str,
    rule_version: str,
    effective_on: date,
) -> RuleApprovalRecord:
    """Read one valid approval from trusted persistence.

    This operation does not grant permission to deduct wages.
    """

    if type(company_id) is not int or company_id <= 0:
        raise TrustedApprovalResolutionError("Invalid company ID")

    if not isinstance(legal_category, str) or not legal_category:
        raise TrustedApprovalResolutionError("Invalid legal category")

    if (
        not isinstance(rule_version, str)
        or not rule_version.strip()
    ):
        raise TrustedApprovalResolutionError("Rule version is required")

    if type(effective_on) is not date:
        raise TrustedApprovalResolutionError("Invalid effective date")

    normalized_version = rule_version.strip()

    statement = select(PayrollDeductionRuleApproval).where(
        PayrollDeductionRuleApproval.company_id == company_id,
        PayrollDeductionRuleApproval.legal_category == legal_category,
        PayrollDeductionRuleApproval.rule_version == normalized_version,
    )

    rows = list((await db.scalars(statement)).all())

    # Fail closed on missing or ambiguous persisted approvals.
    if len(rows) != 1:
        raise TrustedApprovalResolutionError(
            "Exactly one persisted rule approval is required"
        )

    row = rows[0]

    # Independently verify identity, even when a mocked or altered
    # persistence layer returns an unexpected object.
    if (
        row.company_id != company_id
        or row.legal_category != legal_category
        or row.rule_version != normalized_version
    ):
        raise TrustedApprovalResolutionError(
            "Persisted approval identity mismatch"
        )

    try:
        status = RuleApprovalStatus(row.status)

        record = RuleApprovalRecord(
            category=row.legal_category,
            rule_version=row.rule_version,
            legal_reference=row.legal_reference,
            valid_from=row.valid_from,
            valid_to=row.valid_to,
            status=status,
            created_by=row.created_by,
            approved_by=row.approved_by,
            approved_at=row.approved_at,
            revoked_by=row.revoked_by,
            revoked_at=row.revoked_at,
            company_id=row.company_id,
        )

        require_approved_rule_record(
            record,
            category=legal_category,
            effective_on=effective_on,
        )
    except (PayrollDeductionRuleApprovalError, ValueError, TypeError) as exc:
        raise TrustedApprovalResolutionError(
            "Persisted rule approval is not valid and effective"
        ) from exc

    return record
