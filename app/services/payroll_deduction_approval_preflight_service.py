"""Read-only payroll deduction approval preflight.

This service verifies evidence and a persisted rule approval.
It does NOT authorize withholding, accounting, or payment execution.
"""

from dataclasses import dataclass
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.payroll_deduction_evidence_policy import (
    PayrollDeductionEvidencePolicyError,
    resolve_verified_deduction_classification,
)
from app.services.payroll_deduction_trusted_approval_service import (
    TrustedApprovalResolutionError,
    resolve_trusted_rule_approval,
)


class DeductionApprovalPreflightError(ValueError):
    """Evidence and approval could not be reconciled."""


@dataclass(frozen=True)
class DeductionApprovalPreflight:
    company_id: int
    instruction_id: int
    legal_category: str
    rule_version: str
    effective_on: date

    # This result is never a financial execution permit.
    financial_execution_authorized: bool = False


async def verify_deduction_approval_preflight(
    db: AsyncSession,
    *,
    company_id: int,
    instruction_id: int,
    legal_category: str,
    rule_version: str,
    effective_on: date,
) -> DeductionApprovalPreflight:
    """Reconcile verified evidence with a persisted approval.

    Rule version must be selected by trusted application logic.
    Caller-provided values alone never authorize a deduction.
    """

    if type(company_id) is not int or company_id <= 0:
        raise DeductionApprovalPreflightError("Invalid company")

    if type(instruction_id) is not int or instruction_id <= 0:
        raise DeductionApprovalPreflightError("Invalid instruction")

    if not isinstance(legal_category, str) or not legal_category:
        raise DeductionApprovalPreflightError("Invalid category")

    if not isinstance(rule_version, str) or not rule_version.strip():
        raise DeductionApprovalPreflightError("Invalid rule version")

    if type(effective_on) is not date:
        raise DeductionApprovalPreflightError("Invalid effective date")

    try:
        classification = await resolve_verified_deduction_classification(
            db,
            company_id=company_id,
            payroll_deduction_instruction_id=instruction_id,
        )

        if classification.category != legal_category:
            raise DeductionApprovalPreflightError(
                "Verified evidence category does not match approval"
            )

        approval = await resolve_trusted_rule_approval(
            db,
            company_id=company_id,
            legal_category=classification.category,
            rule_version=rule_version,
            effective_on=effective_on,
        )

        if (
            approval.company_id != company_id
            or approval.category != classification.category
            or approval.rule_version != rule_version.strip()
        ):
            raise DeductionApprovalPreflightError(
                "Persisted approval provenance mismatch"
            )

    except (
        PayrollDeductionEvidencePolicyError,
        TrustedApprovalResolutionError,
    ) as exc:
        raise DeductionApprovalPreflightError(
            "Verified evidence and effective approval are required"
        ) from exc

    return DeductionApprovalPreflight(
        company_id=company_id,
        instruction_id=instruction_id,
        legal_category=classification.category,
        rule_version=approval.rule_version,
        effective_on=effective_on,
    )
