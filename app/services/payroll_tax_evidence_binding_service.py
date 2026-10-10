"""Validated binding between tax policies and documentary evidence."""

from datetime import date

from app.services.payroll_tax_evidence_service import (
    PayrollTaxEvidenceError,
    require_valid_tax_evidence,
)


async def require_evidence_window(
    db,
    *,
    company_id: int,
    employee_id: int,
    evidence_id: int,
    entitlement_type: str,
    entitlement_code: str,
    effective_from: date,
    effective_to: date | None,
):
    """Validate verified evidence for the complete policy window."""

    evidence = await require_valid_tax_evidence(
        db,
        company_id=company_id,
        employee_id=employee_id,
        evidence_id=evidence_id,
        entitlement_type=entitlement_type,
        entitlement_code=entitlement_code,
        effective_on=effective_from,
    )

    if effective_to is None:
        if evidence.valid_to is not None:
            raise PayrollTaxEvidenceError(
                "Open-ended policy requires open-ended evidence"
            )
    elif (
        evidence.valid_to is not None
        and effective_to > evidence.valid_to
    ):
        raise PayrollTaxEvidenceError(
            "Evidence expires before policy end"
        )

    return evidence
