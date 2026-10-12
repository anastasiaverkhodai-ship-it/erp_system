"""Validation rules for persisted payroll deduction evidence.

These functions validate evidence record structure.
They do not authenticate documents or authorize deductions.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class PayrollDeductionEvidenceError(ValueError):
    pass


class EvidenceStatus(str, Enum):
    PENDING = "pending"
    VERIFIED = "verified"
    REJECTED = "rejected"


SUPPORTED_CATEGORIES = frozenset({
    "employer_statutory",
    "enforcement_other",
    "enforcement_special",
    "minor_child_alimony",
})


@dataclass(frozen=True)
class EvidenceValidationResult:
    legal_category: str
    document_reference: str
    verification_status: EvidenceStatus
    verified_by: int | None
    verified_at: datetime | None


def validate_evidence(
    *,
    legal_category: str,
    document_reference: str,
    verification_status: str,
    verified_by: int | None,
    verified_at: datetime | None,
) -> EvidenceValidationResult:
    """Validate structural consistency of evidence data."""

    if legal_category not in SUPPORTED_CATEGORIES:
        raise PayrollDeductionEvidenceError(
            "Unsupported legal category"
        )

    if (
        not isinstance(document_reference, str)
        or not document_reference.strip()
    ):
        raise PayrollDeductionEvidenceError(
            "Document reference is required"
        )

    try:
        status = EvidenceStatus(verification_status)
    except ValueError as exc:
        raise PayrollDeductionEvidenceError(
            "Invalid verification status"
        ) from exc

    if status is EvidenceStatus.VERIFIED:
        if verified_by is None or verified_by <= 0:
            raise PayrollDeductionEvidenceError(
                "Verified evidence requires a valid verifier"
            )

        if verified_at is None:
            raise PayrollDeductionEvidenceError(
                "Verified evidence requires verification time"
            )

        if (
            verified_at.tzinfo is None
            or verified_at.utcoffset() is None
        ):
            raise PayrollDeductionEvidenceError(
                "Verification time must be timezone-aware"
            )

    else:
        if verified_by is not None or verified_at is not None:
            raise PayrollDeductionEvidenceError(
                "Unverified evidence cannot contain verification data"
            )

    return EvidenceValidationResult(
        legal_category=legal_category,
        document_reference=document_reference.strip(),
        verification_status=status,
        verified_by=verified_by,
        verified_at=verified_at,
    )
