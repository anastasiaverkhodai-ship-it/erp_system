"""Review-only evidence intake for payment-specific PIT/levy.

No document bytes or external issuer are independently verified
by this service. It must never unlock official 4DF generation.
"""

import re
from decimal import Decimal

from sqlalchemy import select, text

from app.models.payroll import PayrollCalculation
from app.services.payroll_regulatory_payment_evidence import (
    active_posted_journal_query,
)
from app.services.payroll_payment_tax_allocation_candidate import (
    PaymentTaxAllocationError,
    money,
    require_id,
    require_text,
)


TABLE = "payroll_payment_tax_evidence"


def require_digest(value):
    if (
        type(value) is not str
        or re.fullmatch(r"[0-9a-f]{64}", value) is None
    ):
        raise PaymentTaxAllocationError(
            "Invalid SHA-256 digest reference"
        )
    return value


def version_key_for(correction_id):
    if correction_id is None:
        return "original"
    return f"correction:{require_id(correction_id, 'correction')}"


async def register_payment_tax_evidence(
    db,
    *,
    company_id,
    candidate_id,
    correction_id,
    amount,
    source_type,
    source_issuer,
    evidence_reference,
    document_sha256,
    request_key,
    created_by,
):
    company_id = require_id(company_id, "company")
    candidate_id = require_id(candidate_id, "candidate")
    created_by = require_id(created_by, "actor")
    if correction_id is not None:
        correction_id = require_id(correction_id, "correction")

    value = money(amount)
    source_type = require_text(source_type, "source type", 50)
    source_issuer = require_text(source_issuer, "source issuer", 200)
    evidence_reference = require_text(
        evidence_reference, "evidence reference", 500
    )
    digest = require_digest(document_sha256)
    key = require_text(request_key, "request key", 150)
    version = version_key_for(correction_id)

    candidate = (
        await db.execute(
            text("""
                SELECT company_id, id, payroll_calculation_id,
                       payroll_disbursement_id, statutory_line_id,
                       amount
                FROM payroll_payment_tax_allocation_candidates
                WHERE company_id=:company AND id=:candidate
            """),
            {"company": company_id, "candidate": candidate_id},
        )
    ).mappings().first()

    if candidate is None:
        raise PaymentTaxAllocationError(
            "Candidate not found for company"
        )

    calculation = await db.scalar(
        select(PayrollCalculation).where(
            PayrollCalculation.company_id == company_id,
            PayrollCalculation.id
                == candidate["payroll_calculation_id"],
        ).with_for_update()
    )
    if calculation is None or calculation.currency_code != "UAH":
        raise PaymentTaxAllocationError(
            "Source calculation unavailable"
        )

    payment = (
        await db.execute(
            text("""
                SELECT id, payment_date, currency_code,
                       cancelled_at, reversed_on
                FROM payroll_disbursements
                WHERE company_id=:company
                  AND id=:payment
                  AND payroll_calculation_id=:calc
            """),
            {
                "company": company_id,
                "payment": candidate["payroll_disbursement_id"],
                "calc": candidate["payroll_calculation_id"],
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
            "Active salary payment required"
        )

    posted = await db.scalar(
        active_posted_journal_query(
            company_id=company_id,
            payroll_disbursement_id=payment["id"],
        )
    )
    if posted is None:
        raise PaymentTaxAllocationError(
            "Posted payment journal required"
        )

    source = (
        await db.execute(
            text("""
                SELECT l.id, l.component, l.amount
                FROM payroll_statutory_result_lines l
                JOIN payroll_statutory_results r
                  ON r.id=l.payroll_statutory_result_id
                 AND r.company_id=l.company_id
                WHERE l.company_id=:company
                  AND l.id=:line
                  AND r.payroll_calculation_id=:calc
                  AND l.currency_code='UAH'
                  AND l.component IN (
                      'personal_income_tax', 'military_levy'
                  )
            """),
            {
                "company": company_id,
                "line": candidate["statutory_line_id"],
                "calc": candidate["payroll_calculation_id"],
            },
        )
    ).mappings().first()

    if source is None:
        raise PaymentTaxAllocationError(
            "PIT/levy statutory source missing"
        )

    revoked = (
        await db.execute(
            text("""
                SELECT 1
                FROM payroll_payment_tax_allocation_candidate_events
                WHERE company_id=:company
                  AND candidate_id=:candidate
                  AND event_kind='revoked'
            """),
            {"company": company_id, "candidate": candidate_id},
        )
    ).scalar_one_or_none() is not None

    latest = (
        await db.execute(
            text("""
                SELECT id, amount
                FROM payroll_payment_tax_allocation_corrections
                WHERE company_id=:company
                  AND original_candidate_id=:candidate
                ORDER BY id DESC
                LIMIT 1
            """),
            {"company": company_id, "candidate": candidate_id},
        )
    ).mappings().first()

    expected_correction = None if latest is None else latest["id"]
    effective = (
        Decimal(candidate["amount"])
        if latest is None
        else Decimal(latest["amount"])
    )

    if expected_correction != correction_id:
        raise PaymentTaxAllocationError(
            "Evidence references stale correction version"
        )

    if revoked and latest is None:
        raise PaymentTaxAllocationError(
            "Revoked allocation without replacement"
        )

    if value != effective:
        raise PaymentTaxAllocationError(
            "Evidence amount differs from effective allocation"
        )

    if value > Decimal(source["amount"]):
        raise PaymentTaxAllocationError(
            "Evidence exceeds statutory source"
        )

    existing = (
        await db.execute(
            text(f"""
                SELECT id, candidate_id, correction_id,
                       version_key, amount, source_type,
                       source_issuer, evidence_reference,
                       document_sha256, created_by
                FROM {TABLE}
                WHERE company_id=:company AND request_key=:key
            """),
            {"company": company_id, "key": key},
        )
    ).mappings().first()

    if existing is not None:
        if (
            existing["candidate_id"] != candidate_id
            or existing["correction_id"] != correction_id
            or existing["version_key"] != version
            or Decimal(existing["amount"]) != value
            or existing["source_type"] != source_type
            or existing["source_issuer"] != source_issuer
            or existing["evidence_reference"] != evidence_reference
            or existing["document_sha256"] != digest
            or existing["created_by"] != created_by
        ):
            raise PaymentTaxAllocationError(
                "Evidence idempotency conflict"
            )
        return {
            "evidence_id": existing["id"],
            "idempotent": True,
            "verification_status": "unverified",
            "official_generation": "BLOCKED",
        }

    duplicate = (
        await db.execute(
            text(f"""
                SELECT id FROM {TABLE}
                WHERE company_id=:company
                  AND candidate_id=:candidate
                  AND version_key=:version
            """),
            {
                "company": company_id,
                "candidate": candidate_id,
                "version": version,
            },
        )
    ).scalar_one_or_none()
    if duplicate is not None:
        raise PaymentTaxAllocationError(
            "Evidence already registered for version"
        )

    inserted = await db.execute(
        text(f"""
            INSERT INTO {TABLE} (
                company_id, candidate_id, correction_id,
                version_key, payroll_disbursement_id,
                statutory_line_id, amount, payment_date,
                source_type, source_issuer, evidence_reference,
                document_sha256, request_key, created_by,
                verification_status
            ) VALUES (
                :company, :candidate, :correction,
                :version, :payment, :line, :amount,
                :payment_date, :source_type, :issuer,
                :reference, :digest, :key, :actor,
                'unverified'
            ) RETURNING id
        """),
        {
            "company": company_id,
            "candidate": candidate_id,
            "correction": correction_id,
            "version": version,
            "payment": payment["id"],
            "line": source["id"],
            "amount": value,
            "payment_date": payment["payment_date"],
            "source_type": source_type,
            "issuer": source_issuer,
            "reference": evidence_reference,
            "digest": digest,
            "key": key,
            "actor": created_by,
        },
    )

    return {
        "evidence_id": inserted.scalar_one(),
        "idempotent": False,
        "candidate_id": candidate_id,
        "correction_id": correction_id,
        "source_payment_id": payment["id"],
        "payment_date": str(payment["payment_date"]),
        "source_component": source["component"],
        "amount": str(value),
        "verification_status": "unverified",
        "payment_specific_withholding_verified": False,
        "gross_paid_approved": False,
        "official_generation": "BLOCKED",
    }
