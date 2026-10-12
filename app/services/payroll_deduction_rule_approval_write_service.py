"""Authorized, company-scoped payroll deduction rule approval writes.

Security boundaries:
- The actor ID must come from authenticated application context.
- This service checks current company-scoped RBAC permissions itself.
- The service never commits or rolls back its caller's transaction.
- PostgreSQL lifecycle triggers maintain the approval audit history.
- Approval does NOT activate automatic payroll deductions.
"""

from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.payroll_deduction_rule_approval import (
    PayrollDeductionRuleApproval,
)
from app.models.permission import Permission
from app.models.rbac import role_permissions
from app.models.user_company_role import UserCompanyRole
from app.services.payroll_deduction_rule_approval_contract import (
    PayrollDeductionRuleApprovalError,
    RuleApprovalRecord,
    RuleApprovalStatus,
    validate_rule_approval,
)


CREATE_PERMISSION = "payroll_deduction_rule_approval.create"
APPROVE_PERMISSION = "payroll_deduction_rule_approval.approve"
REVOKE_PERMISSION = "payroll_deduction_rule_approval.revoke"


class PayrollDeductionRuleApprovalWriteError(
    PayrollDeductionRuleApprovalError
):
    """Invalid or unauthorized payroll deduction approval operation."""



def _validate_write_record(record: RuleApprovalRecord) -> None:
    """Translate approval contract errors into write-service errors."""
    try:
        validate_rule_approval(record)
    except PayrollDeductionRuleApprovalError as exc:
        raise PayrollDeductionRuleApprovalWriteError(str(exc)) from exc


def _require_positive_id(value: int, label: str) -> None:
    if type(value) is not int or value <= 0:
        raise PayrollDeductionRuleApprovalWriteError(
            f"Invalid {label}"
        )


async def _require_permission(
    *,
    db: AsyncSession,
    company_id: int,
    actor_id: int,
    permission: str,
) -> None:
    _require_positive_id(company_id, "company ID")
    _require_positive_id(actor_id, "authenticated actor ID")

    statement = (
        select(Permission.id)
        .join(
            role_permissions,
            Permission.id == role_permissions.c.permission_id,
        )
        .join(
            UserCompanyRole,
            UserCompanyRole.role_id == role_permissions.c.role_id,
        )
        .where(
            UserCompanyRole.company_id == company_id,
            UserCompanyRole.user_id == actor_id,
            UserCompanyRole.is_active.is_(True),
            Permission.name == permission,
        )
        .limit(1)
    )

    result = await db.execute(statement)

    if result.scalar_one_or_none() is None:
        raise PayrollDeductionRuleApprovalWriteError(
            "Permission denied for this company"
        )


def _record_from_model(
    approval: PayrollDeductionRuleApproval,
) -> RuleApprovalRecord:
    try:
        status = RuleApprovalStatus(approval.status)
    except (ValueError, TypeError) as exc:
        raise PayrollDeductionRuleApprovalWriteError(
            "Unknown persisted approval status"
        ) from exc

    return RuleApprovalRecord(
        category=approval.legal_category,
        rule_version=approval.rule_version,
        legal_reference=approval.legal_reference,
        valid_from=approval.valid_from,
        valid_to=approval.valid_to,
        status=status,
        created_by=approval.created_by,
        approved_by=approval.approved_by,
        approved_at=approval.approved_at,
        revoked_by=approval.revoked_by,
        revoked_at=approval.revoked_at,
    )


async def _locked_approval(
    *,
    db: AsyncSession,
    company_id: int,
    approval_id: int,
) -> PayrollDeductionRuleApproval:
    _require_positive_id(approval_id, "approval ID")

    approval = await db.scalar(
        select(PayrollDeductionRuleApproval)
        .where(
            PayrollDeductionRuleApproval.id == approval_id,
            PayrollDeductionRuleApproval.company_id == company_id,
        )
        .with_for_update()
    )

    if approval is None:
        raise PayrollDeductionRuleApprovalWriteError(
            "Company-scoped approval record not found"
        )

    return approval


async def create_pending_rule_approval(
    *,
    db: AsyncSession,
    company_id: int,
    legal_category: str,
    rule_version: str,
    legal_reference: str,
    valid_from: date,
    valid_to: date | None,
    actor_id: int,
) -> PayrollDeductionRuleApproval:
    """Create a pending approval; permission is checked against DB RBAC."""

    await _require_permission(
        db=db,
        company_id=company_id,
        actor_id=actor_id,
        permission=CREATE_PERMISSION,
    )

    record = RuleApprovalRecord(
        category=legal_category,
        rule_version=rule_version,
        legal_reference=legal_reference,
        valid_from=valid_from,
        valid_to=valid_to,
        status=RuleApprovalStatus.PENDING,
        created_by=actor_id,
    )
    _validate_write_record(record)

    approval = PayrollDeductionRuleApproval(
        company_id=company_id,
        legal_category=legal_category,
        rule_version=rule_version.strip(),
        legal_reference=legal_reference.strip(),
        valid_from=valid_from,
        valid_to=valid_to,
        status=RuleApprovalStatus.PENDING.value,
        created_by=actor_id,
        created_at=datetime.now(timezone.utc),
        approved_by=None,
        approved_at=None,
        revoked_by=None,
        revoked_at=None,
    )

    db.add(approval)
    await db.flush()
    return approval


async def approve_rule_approval(
    *,
    db: AsyncSession,
    company_id: int,
    approval_id: int,
    actor_id: int,
) -> PayrollDeductionRuleApproval:
    """Approve a pending rule with independent, authorized actor."""

    await _require_permission(
        db=db,
        company_id=company_id,
        actor_id=actor_id,
        permission=APPROVE_PERMISSION,
    )

    approval = await _locked_approval(
        db=db,
        company_id=company_id,
        approval_id=approval_id,
    )

    if approval.status != RuleApprovalStatus.PENDING.value:
        raise PayrollDeductionRuleApprovalWriteError(
            "Only pending rules can be approved"
        )

    if actor_id == approval.created_by:
        raise PayrollDeductionRuleApprovalWriteError(
            "Creator cannot approve their own rule"
        )

    # Validate persisted state before changing it.
    _validate_write_record(_record_from_model(approval))

    approval.status = RuleApprovalStatus.APPROVED.value
    approval.approved_by = actor_id
    approval.approved_at = datetime.now(timezone.utc)

    _validate_write_record(_record_from_model(approval))
    await db.flush()
    return approval


async def revoke_rule_approval(
    *,
    db: AsyncSession,
    company_id: int,
    approval_id: int,
    actor_id: int,
) -> PayrollDeductionRuleApproval:
    """Revoke an approved rule; approval history remains append-only."""

    await _require_permission(
        db=db,
        company_id=company_id,
        actor_id=actor_id,
        permission=REVOKE_PERMISSION,
    )

    approval = await _locked_approval(
        db=db,
        company_id=company_id,
        approval_id=approval_id,
    )

    if approval.status != RuleApprovalStatus.APPROVED.value:
        raise PayrollDeductionRuleApprovalWriteError(
            "Only approved rules can be revoked"
        )

    _validate_write_record(_record_from_model(approval))

    now = datetime.now(timezone.utc)

    if now < approval.approved_at:
        raise PayrollDeductionRuleApprovalWriteError(
            "Revocation cannot precede approval"
        )

    approval.status = RuleApprovalStatus.REVOKED.value
    approval.revoked_by = actor_id
    approval.revoked_at = now

    _validate_write_record(_record_from_model(approval))
    await db.flush()
    return approval
