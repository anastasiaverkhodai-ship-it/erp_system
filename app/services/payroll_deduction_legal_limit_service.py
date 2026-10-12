"""Evaluate candidate limits using persisted verified legal evidence.

This module does not authorize a payroll deduction.
It does not change payroll results or manage transactions.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.payroll_deduction_evidence_policy import (
    resolve_verified_deduction_classification,
)
from app.services.payroll_deduction_verified_limit import (
    VerifiedDeductionLimit,
    calculate_verified_deduction_limit,
)


async def evaluate_verified_deduction_limit(
    db: AsyncSession,
    *,
    company_id: int,
    payroll_deduction_instruction_id: int,
    statutory_net: Decimal,
    requested_amount: Decimal,
    already_withheld: Decimal,
) -> VerifiedDeductionLimit:
    """Evaluate a candidate limit for one verified instruction.

    A caller must separately establish:
    - Legal applicability of the classification.
    - Priority among multiple deductions.
    - The correct aggregate cap for the entire payroll.
    """

    classification = await resolve_verified_deduction_classification(
        db,
        company_id=company_id,
        payroll_deduction_instruction_id=payroll_deduction_instruction_id,
    )

    return calculate_verified_deduction_limit(
        statutory_net=statutory_net,
        requested_amount=requested_amount,
        already_withheld=already_withheld,
        classification=classification,
    )
