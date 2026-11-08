"""Persist signed receipt identity after source-bound verification.

Does not approve issuer identity, withholding or statutory
generation. Transaction ownership belongs to the caller.
"""

import base64
import binascii
import hashlib
from datetime import date

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.services.payroll_payment_tax_allocation_candidate import (
    PaymentTaxAllocationError,
    require_id,
)
from app.services.payroll_payment_tax_external_binding import (
    verify_external_source_for_evidence,
)
from app.services.payroll_payment_tax_external_signature import (
    canonical_payload,
)


TABLE = "payroll_external_receipt_registry"


async def register_external_receipt(
    db,
    *,
    company_id,
    evidence_id,
    signed_payload,
    signature_b64,
):
    company_id = require_id(company_id, "company")
    evidence_id = require_id(evidence_id, "evidence")

    result = await verify_external_source_for_evidence(
        db,
        company_id=company_id,
        evidence_id=evidence_id,
        signed_payload=signed_payload,
        signature_b64=signature_b64,
    )

    if (
        result["official_generation"] != "BLOCKED"
        or result["actual_withholding"] != "NOT_VERIFIED"
        or result["issuer_identity"] != "NOT_VERIFIED"
    ):
        raise PaymentTaxAllocationError(
            "Unsafe external receipt approval"
        )

    payload = canonical_payload(signed_payload)

    try:
        signature = base64.b64decode(
            signature_b64, validate=True
        )
    except (ValueError, binascii.Error) as exc:
        raise PaymentTaxAllocationError(
            "Invalid receipt signature"
        ) from exc

    if len(signature) != 64:
        raise PaymentTaxAllocationError(
            "Invalid receipt signature length"
        )

    # Verify from the database-derived result rather than
    # trusting an arbitrary client-selected payment identity.
    if (
        signed_payload["payment_id"] != result["payment_id"]
        or signed_payload["component"]
            != result["source_component"]
        or signed_payload["company_id"] != company_id
    ):
        raise PaymentTaxAllocationError(
            "External receipt source mismatch"
        )

    # A unique constraint enforces replay protection atomically
    # across sessions and companies, not just within a batch.
    # Savepoint prevents a duplicate from aborting outer work.
    try:
        async with db.begin_nested():
            inserted = await db.execute(
                text(f"""
                    INSERT INTO {TABLE} (
                        company_id, payroll_disbursement_id,
                        evidence_id, issuer_id, key_id, receipt_id,
                        component, amount, payment_date,
                        source_date, signed_payload_sha256,
                        signature_sha256
                    ) VALUES (
                        :company, :payment, :evidence,
                        :issuer, :key, :receipt,
                        :component, :amount, :payment_date,
                        :source_date, :payload_hash,
                        :signature_hash
                    )
                    RETURNING id
                """),
                {
                    "company": company_id,
                    "payment": result["payment_id"],
                    "evidence": evidence_id,
                    "issuer": signed_payload["issuer_id"],
                    "key": signed_payload["key_id"],
                    "receipt": signed_payload["receipt_id"],
                    "component": result["source_component"],
                    "amount": signed_payload["amount"],
                    "payment_date": date.fromisoformat(
                        signed_payload["payment_date"]
                    ),
                    "source_date": date.fromisoformat(
                        signed_payload["source_date"]
                    ),
                    "payload_hash": hashlib.sha256(
                        payload
                    ).hexdigest(),
                    "signature_hash": hashlib.sha256(
                        signature
                    ).hexdigest(),
                },
            )
            registry_id = inserted.scalar_one()

    except IntegrityError as exc:
        raise PaymentTaxAllocationError(
            "External receipt replay or evidence conflict"
        ) from exc

    return {
        "registry_id": registry_id,
        "company_id": company_id,
        "evidence_id": evidence_id,
        "receipt_registered": True,
        "signature_cryptographically_valid": True,
        "issuer_identity": "NOT_VERIFIED",
        "actual_withholding": "NOT_VERIFIED",
        "gross_paid_attribution": "NOT_APPROVED",
        "official_generation": "BLOCKED",
    }
