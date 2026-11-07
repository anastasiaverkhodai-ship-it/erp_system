"""Document integrity and internal independent review.

Never interprets a hash match or reviewer decision as
proof that PIT/levy was actually withheld at payment.
"""

import hashlib
from sqlalchemy import select, text

from app.models.payroll import PayrollCalculation
from app.services.payroll_regulatory_payment_evidence import (
    active_posted_journal_query,
)
from app.services.payroll_payment_tax_allocation_candidate import (
    PaymentTaxAllocationError,
    require_id,
    require_text,
)


INTEGRITY = "payroll_payment_tax_document_integrity"
REVIEWS = "payroll_payment_tax_document_reviews"


def document_fingerprint(document_bytes):
    if type(document_bytes) is not bytes:
        raise PaymentTaxAllocationError(
            "Document must be raw bytes"
        )

    if not 1 <= len(document_bytes) <= 5 * 1024 * 1024:
        raise PaymentTaxAllocationError(
            "Invalid document size"
        )

    return (
        hashlib.sha256(document_bytes).hexdigest(),
        len(document_bytes),
    )


async def _source(db, company_id, evidence_id):
    evidence = (
        await db.execute(
            text("""
                SELECT e.id, e.candidate_id, e.correction_id,
                       e.document_sha256, e.created_by,
                       e.payroll_disbursement_id,
                       e.stat_utory_line_placeholder,
                       c.created_by AS candidate_created_by,
                       c.payroll_calculation_id
                FROM payroll_payment_tax_evidence e
                JOIN payroll_payment_tax_allocation_candidates c
                  ON c.id=e.candidate_id
                 AND c.company_id=e.company_id
                WHERE e.company_id=:company
                  AND e.id=:evidence
            """.replace(
                "e.stat_utory_line_placeholder",
                "e.statutory_line_id"
            )),
            {
                "company": company_id,
                "evidence": evidence_id,
            },
        )
    ).mappings().first()

    if evidence is None:
        raise PaymentTaxAllocationError(
            "Evidence not found for company"
        )

    calc = await db.scalar(
        select(PayrollCalculation).where(
            PayrollCalculation.company_id == company_id,
            PayrollCalculation.id
                == evidence["payroll_calculation_id"],
        ).with_for_update()
    )
    if calc is None or calc.currency_code != "UAH":
        raise PaymentTaxAllocationError(
            "Payroll calculation unavailable"
        )

    payment = (
        await db.execute(
            text("""
                SELECT payment_date, cancelled_at,
                       reversed_on, currency_code
                FROM payroll_disbursements
                WHERE company_id=:company
                  AND id=:payment
                  AND payroll_calculation_id=:calc
            """),
            {
                "company": company_id,
                "payment": evidence["payroll_disbursement_id"],
                "calc": evidence["payroll_calculation_id"],
            },
        )
    ).mappings().first()

    if (
        payment is None
        or payment["cancelled_at"] is not None
        or payment["reversed_on"] is not None
        or payment["currency_code"] != "UAH"
    ):
        raise PaymentTaxAllocationError(
            "Active payment required"
        )

    posted = await db.scalar(
        active_posted_journal_query(
            company_id=company_id,
            payroll_disbursement_id
                =evidence["payroll_disbursement_id"],
        )
    )
    if posted is None:
        raise PaymentTaxAllocationError(
            "Posted journal required"
        )

    latest = (
        await db.execute(
            text("""
                SELECT id
                FROM payroll_payment_tax_allocation_corrections
                WHERE company_id=:company
                  AND original_candidate_id=:candidate
                ORDER BY id DESC LIMIT 1
            """),
            {
                "company": company_id,
                "candidate": evidence["candidate_id"],
            },
        )
    ).scalar_one_or_none()

    if latest != evidence["correction_id"]:
        raise PaymentTaxAllocationError(
            "Evidence version is stale"
        )

    return evidence


async def record_document_integrity(
    db,
    *,
    company_id,
    evidence_id,
    document_bytes,
    request_key,
    checked_by,
):
    company_id = require_id(company_id, "company")
    evidence_id = require_id(evidence_id, "evidence")
    checked_by = require_id(checked_by, "checker")
    key = require_text(request_key, "request key", 150)

    digest, size = document_fingerprint(document_bytes)
    source = await _source(db, company_id, evidence_id)

    if digest != source["document_sha256"]:
        raise PaymentTaxAllocationError(
            "Document SHA-256 mismatch"
        )

    previous = (
        await db.execute(
            text(f"""
                SELECT id, evidence_id, document_sha256,
                       document_size_bytes, checked_by
                FROM {INTEGRITY}
                WHERE company_id=:company AND request_key=:key
            """),
            {"company": company_id, "key": key},
        )
    ).mappings().first()

    if previous is not None:
        if (
            previous["evidence_id"] != evidence_id
            or previous["document_sha256"] != digest
            or previous["document_size_bytes"] != size
            or previous["checked_by"] != checked_by
        ):
            raise PaymentTaxAllocationError(
                "Integrity idempotency conflict"
            )
        return {
            "integrity_check_id": previous["id"],
            "idempotent": True,
            "status": "hash_matched_only",
            "official_generation": "BLOCKED",
        }

    inserted = await db.execute(
        text(f"""
            INSERT INTO {INTEGRITY} (
                company_id, evidence_id, document_sha256,
                document_size_bytes, request_key,
                checked_by, check_status
            ) VALUES (
                :company, :evidence, :digest,
                :size, :key, :checker, 'hash_matched_only'
            ) RETURNING id
        """),
        {
            "company": company_id,
            "evidence": evidence_id,
            "digest": digest,
            "size": size,
            "key": key,
            "checker": checked_by,
        },
    )

    return {
        "integrity_check_id": inserted.scalar_one(),
        "idempotent": False,
        "document_sha256": digest,
        "document_size_bytes": size,
        "status": "hash_matched_only",
        "issuer_verified": False,
        "payment_specific_withholding_verified": False,
        "official_generation": "BLOCKED",
    }


async def record_independent_document_review(
    db,
    *,
    company_id,
    integrity_check_id,
    reviewed_by,
    review_reference,
    request_key,
):
    company_id = require_id(company_id, "company")
    integrity_check_id = require_id(
        integrity_check_id, "integrity check"
    )
    reviewed_by = require_id(reviewed_by, "reviewer")
    review_reference = require_text(
        review_reference, "review reference", 500
    )
    key = require_text(request_key, "request key", 150)

    integrity = (
        await db.execute(
            text(f"""
                SELECT id, evidence_id, checked_by
                FROM {INTEGRITY}
                WHERE company_id=:company AND id=:check
            """),
            {
                "company": company_id,
                "check": integrity_check_id,
            },
        )
    ).mappings().first()

    if integrity is None:
        raise PaymentTaxAllocationError(
            "Integrity check not found"
        )

    source = await _source(
        db, company_id, integrity["evidence_id"]
    )

    if reviewed_by in (
        integrity["checked_by"],
        source["created_by"],
        source["candidate_created_by"],
    ):
        raise PaymentTaxAllocationError(
            "Independent reviewer required"
        )

    previous = (
        await db.execute(
            text(f"""
                SELECT id, integrity_check_id, evidence_id,
                       reviewed_by, review_reference
                FROM {REVIEWS}
                WHERE company_id=:company AND request_key=:key
            """),
            {"company": company_id, "key": key},
        )
    ).mappings().first()

    if previous is not None:
        if (
            previous["integrity_check_id"] != integrity_check_id
            or previous["evidence_id"] != integrity["evidence_id"]
            or previous["reviewed_by"] != reviewed_by
            or previous["review_reference"] != review_reference
        ):
            raise PaymentTaxAllocationError(
                "Review idempotency conflict"
            )
        return {
            "review_id": previous["id"],
            "idempotent": True,
            "status": "internal_review_only",
            "official_generation": "BLOCKED",
        }

    inserted = await db.execute(
        text(f"""
            INSERT INTO {REVIEWS} (
                company_id, integrity_check_id, evidence_id,
                reviewed_by, review_reference, request_key,
                review_status
            ) VALUES (
                :company, :check, :evidence,
                :reviewer, :reference, :key,
                'internal_review_only'
            ) RETURNING id
        """),
        {
            "company": company_id,
            "check": integrity_check_id,
            "evidence": integrity["evidence_id"],
            "reviewer": reviewed_by,
            "reference": review_reference,
            "key": key,
        },
    )

    return {
        "review_id": inserted.scalar_one(),
        "idempotent": False,
        "status": "internal_review_only",
        "issuer_verified": False,
        "payment_specific_withholding_verified": False,
        "official_generation": "BLOCKED",
    }
