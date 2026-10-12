"""C2 database-backed approval resolution for deduction previews.

Reads actual approval records from PostgreSQL through AsyncSession.
Every lookup is scoped to the requested company.

This module never grants authorization to post payroll, never
changes approval status, and never commits or flushes.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.payroll_deduction_rule_approval import (
    PayrollDeductionRuleApproval,
)
from app.services.payroll_deduction_aggregation_policy import (
    DeductionPolicyLine,
)
from app.services.payroll_deduction_aggregation_integration import (
    ApprovedDeductionLine,
    DeductionIntegrationPreview,
    preview_approved_deduction_plan,
)
from app.services.payroll_deduction_rule_approval_write_service import (
    _record_from_model,
)


class DeductionApprovalResolutionError(ValueError):
    """Approval references are missing or invalid."""


@dataclass(frozen=True)
class PersistedDeductionLine:
    policy_line: DeductionPolicyLine
    approval_id: int


async def preview_deduction_plan_from_db(
    *,
    db: AsyncSession,
    company_id: int,
    effective_on: date,
    available_amount: Decimal,
    aggregate_limit: Decimal,
    lines: tuple[PersistedDeductionLine, ...],
) -> DeductionIntegrationPreview:
    """Resolve company-scoped database approvals and preview a plan.

    Approval rows must be persisted and approved. The downstream
    contract checks status, category and effective dates.

    A legal instruction-to-document binding is NOT established here.
    This function cannot be used to authorize payroll posting.
    """

    if type(company_id) is not int or company_id <= 0:
        raise DeductionApprovalResolutionError(
            "Invalid company_id"
        )

    if type(effective_on) is not date:
        raise DeductionApprovalResolutionError(
            "Invalid effective_on"
        )

    if type(lines) is not tuple:
        raise DeductionApprovalResolutionError(
            "lines must be a tuple"
        )

    identifiers = set()

    for entry in lines:
        if not isinstance(entry, PersistedDeductionLine):
            raise DeductionApprovalResolutionError(
                "Invalid persisted deduction line"
            )

        if not isinstance(entry.policy_line, DeductionPolicyLine):
            raise DeductionApprovalResolutionError(
                "Invalid policy line"
            )

        if type(entry.approval_id) is not int or entry.approval_id <= 0:
            raise DeductionApprovalResolutionError(
                "Invalid approval_id"
            )

        if entry.approval_id in identifiers:
            raise DeductionApprovalResolutionError(
                "One approval cannot be reused across preview lines"
            )

        identifiers.add(entry.approval_id)

    resolved = {}

    if identifiers:
        statement = select(
            PayrollDeductionRuleApproval
        ).where(
            PayrollDeductionRuleApproval.company_id == company_id,
            PayrollDeductionRuleApproval.id.in_(identifiers),
        )

        result = await db.execute(statement)
        records = result.scalars().all()

        resolved = {
            record.id: record
            for record in records
        }

        if len(resolved) != len(identifiers):
            raise DeductionApprovalResolutionError(
                "One or more company-scoped approvals were not found"
            )

    approved_lines = tuple(
        ApprovedDeductionLine(
            policy_line=entry.policy_line,
            approval=_record_from_model(
                resolved[entry.approval_id]
            ),
        )
        for entry in lines
    )

    result = preview_approved_deduction_plan(
        company_id=company_id,
        effective_on=effective_on,
        available_amount=available_amount,
        aggregate_limit=aggregate_limit,
        lines=approved_lines,
    )

    if result.payroll_posting_allowed:
        raise DeductionApprovalResolutionError(
            "Unexpected posting authorization"
        )

    return result
