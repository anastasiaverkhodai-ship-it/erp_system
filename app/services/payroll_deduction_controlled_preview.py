"""Read-only preview of candidate legal payroll deduction limits.

This module never posts payroll results, creates deduction lines,
or commits/rolls back database transactions.

Candidate legal limits remain unapproved for automatic payroll use.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.payroll_deduction_legal_limit_service import (
    evaluate_verified_deduction_limit,
)


class PayrollDeductionPreviewError(ValueError):
    """Controlled payroll preview is unavailable."""


@dataclass(frozen=True)
class ControlledDeductionPreview:
    category: str
    requested_amount: Decimal
    permitted_amount: Decimal
    unapplied_amount: Decimal
    automatic_application_allowed: bool


async def preview_payroll_deduction(
    db: AsyncSession,
    *,
    company_id: int,
    payroll_deduction_instruction_id: int,
    statutory_net: Decimal,
    requested_amount: Decimal,
    already_withheld: Decimal,
) -> ControlledDeductionPreview:
    """Calculate a candidate limit without authorizing payroll writes."""

    if not isinstance(company_id, int) or isinstance(company_id, bool):
        raise PayrollDeductionPreviewError(
            "Valid company ID required"
        )

    if company_id <= 0:
        raise PayrollDeductionPreviewError(
            "Valid company ID required"
        )

    if (
        not isinstance(payroll_deduction_instruction_id, int)
        or isinstance(payroll_deduction_instruction_id, bool)
        or payroll_deduction_instruction_id <= 0
    ):
        raise PayrollDeductionPreviewError(
            "Valid deduction instruction ID required"
        )

    result = await evaluate_verified_deduction_limit(
        db,
        company_id=company_id,
        payroll_deduction_instruction_id=(
            payroll_deduction_instruction_id
        ),
        statutory_net=statutory_net,
        requested_amount=requested_amount,
        already_withheld=already_withheld,
    )

    return ControlledDeductionPreview(
        category=result.category,
        requested_amount=result.requested_amount,
        permitted_amount=result.permitted_amount,
        unapplied_amount=result.unapplied_amount,
        automatic_application_allowed=False,
    )
