"""Encrypted, company-authorized custody of payroll tax documents.

No implicit statutory approval is possible from these operations.
A key must be provisioned externally at runtime.
"""

import base64
import binascii
import hashlib
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import text

from app.services.payroll_payment_tax_allocation_candidate import (
    PaymentTaxAllocationError,
    require_id,
    require_text,
)
from app.services.payroll_payment_tax_document_integrity import (
    _source,
    document_fingerprint,
    record_independent_document_review,
)


TABLE = "payroll_payment_tax_secure_custody"


def _key():
    encoded = os.environ.get("ERP_PAYROLL_CUSTODY_KEY_B64")
    key_id = os.environ.get("ERP_PAYROLL_CUSTODY_KEY_ID")

    if not encoded or not key_id:
        raise PaymentTaxAllocationError(
            "Secure custody encryption key unavailable"
        )

    key_id = require_text(key_id, "key id", 100)

    try:
        key = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise PaymentTaxAllocationError(
            "Invalid encrypted custody key"
        ) from exc

    if len(key) != 32:
        raise PaymentTaxAllocationError(
            "AES-256 custody key required"
        )

    return key_id, key


def _aad(company_id, evidence_id, digest):
    return (
        f"erp:p13:payroll-tax:{company_id}:"
        f"{evidence_id}:{digest}"
    ).encode("ascii")


async def _permission(db, company_id, actor_id, name):
    company_id = require_id(company_id, "company")
    actor_id = require_id(actor_id, "actor")

    permitted = await db.scalar(
        text("""
            SELECT pptsc_authorized(:actor,:company,:permission)
        """),
        {
            "actor": actor_id,
            "company": company_id,
            "permission": name,
        },
    )

    if permitted is not True:
        raise PaymentTaxAllocationError(
            "Company permission denied"
        )


async def store_encrypted_document(
    db,
    *,
    company_id,
    evidence_id,
    integrity_check_id,
    document_bytes,
    created_by,
    request_key,
):
    company_id = require_id(company_id, "company")
    evidence_id = require_id(evidence_id, "evidence")
    integrity_check_id = require_id(
        integrity_check_id, "integrity"
    )
    created_by = require_id(created_by, "actor")
    request_key = require_text(
        request_key, "request key", 150
    )

    await _permission(
        db, company_id, created_by, "employees.manage"
    )

    source = await _source(db, company_id, evidence_id)
    digest, size = document_fingerprint(document_bytes)

    if digest != source["document_sha256"]:
        raise PaymentTaxAllocationError(
            "Document digest differs from evidence"
        )

    integrity = (
        await db.execute(
            text("""
                SELECT id, document_sha256,
                       document_size_bytes
                FROM payroll_payment_tax_document_integrity
                WHERE company_id=:company
                  AND id=:integrity
                  AND evidence_id=:evidence
            """),
            {
                "company": company_id,
                "integrity": integrity_check_id,
                "evidence": evidence_id,
            },
        )
    ).mappings().first()

    if (
        integrity is None
        or integrity["document_sha256"] != digest
        or integrity["document_size_bytes"] != size
    ):
        raise PaymentTaxAllocationError(
            "Independent integrity check required"
        )

    previous = (
        await db.execute(
            text(f"""
                SELECT id, integrity_check_id,
                       document_sha256,
                       document_size_bytes,
                       created_by, request_key
                FROM {TABLE}
                WHERE company_id=:company
                  AND evidence_id=:evidence
            """),
            {
                "company": company_id,
                "evidence": evidence_id,
            },
        )
    ).mappings().first()

    if previous is not None:
        if (
            previous["integrity_check_id"] != integrity_check_id
            or previous["document_sha256"] != digest
            or previous["document_size_bytes"] != size
            or previous["created_by"] != created_by
            or previous["request_key"] != request_key
        ):
            raise PaymentTaxAllocationError(
                "Custody already exists with different identity"
            )

        return {
            "custody_id": previous["id"],
            "idempotent": True,
            "official_generation": "BLOCKED",
        }

    key_id, key = _key()
    nonce = os.urandom(12)
    ciphertext = AESGCM(key).encrypt(
        nonce,
        document_bytes,
        _aad(company_id, evidence_id, digest),
    )

    result = await db.execute(
        text(f"""
            INSERT INTO {TABLE} (
                company_id, evidence_id,
                integrity_check_id,
                key_id, nonce, ciphertext,
                document_sha256, document_size_bytes,
                created_by, request_key
            ) VALUES (
                :company, :evidence, :integrity,
                :key_id, :nonce, :ciphertext,
                :digest, :size, :actor, :request_key
            ) RETURNING id
        """),
        {
            "company": company_id,
            "evidence": evidence_id,
            "integrity": integrity_check_id,
            "key_id": key_id,
            "nonce": nonce,
            "ciphertext": ciphertext,
            "digest": digest,
            "size": size,
            "actor": created_by,
            "request_key": request_key,
        },
    )

    return {
        "custody_id": result.scalar_one(),
        "idempotent": False,
        "encryption": "AES-256-GCM",
        "verification": "integrity_only",
        "official_generation": "BLOCKED",
    }


async def retrieve_encrypted_document(
    db,
    *,
    company_id,
    evidence_id,
    actor_id,
):
    company_id = require_id(company_id, "company")
    evidence_id = require_id(evidence_id, "evidence")
    actor_id = require_id(actor_id, "actor")

    await _permission(
        db, company_id, actor_id, "employees.manage"
    )

    source = await _source(db, company_id, evidence_id)

    custody = (
        await db.execute(
            text(f"""
                SELECT key_id, nonce, ciphertext,
                       document_sha256, document_size_bytes
                FROM {TABLE}
                WHERE company_id=:company
                  AND evidence_id=:evidence
            """),
            {
                "company": company_id,
                "evidence": evidence_id,
            },
        )
    ).mappings().first()

    if custody is None:
        raise PaymentTaxAllocationError(
            "Custodied document unavailable"
        )

    key_id, key = _key()

    if key_id != custody["key_id"]:
        raise PaymentTaxAllocationError(
            "Custody key version unavailable"
        )

    if custody["document_sha256"] != source["document_sha256"]:
        raise PaymentTaxAllocationError(
            "Custody source digest mismatch"
        )

    try:
        document = AESGCM(key).decrypt(
            bytes(custody["nonce"]),
            bytes(custody["ciphertext"]),
            _aad(
                company_id,
                evidence_id,
                custody["document_sha256"],
            ),
        )
    except (InvalidTag, ValueError) as exc:
        raise PaymentTaxAllocationError(
            "Encrypted custody authentication failed"
        ) from exc

    digest, size = document_fingerprint(document)

    if (
        digest != custody["document_sha256"]
        or size != custody["document_size_bytes"]
    ):
        raise PaymentTaxAllocationError(
            "Retrieved document integrity mismatch"
        )

    return document


async def review_custodied_document(
    db,
    *,
    company_id,
    integrity_check_id,
    evidence_id,
    reviewed_by,
    review_reference,
    request_key,
):
    company_id = require_id(company_id, "company")
    evidence_id = require_id(evidence_id, "evidence")
    integrity_check_id = require_id(
        integrity_check_id, "integrity"
    )
    reviewed_by = require_id(reviewed_by, "reviewer")

    await _permission(
        db, company_id, reviewed_by, "employees.manage"
    )
    await _permission(
        db, company_id, reviewed_by,
        "journal_entries.approve",
    )

    custody = await db.scalar(
        text(f"""
            SELECT id FROM {TABLE}
            WHERE company_id=:company
              AND evidence_id=:evidence
              AND integrity_check_id=:integrity
        """),
        {
            "company": company_id,
            "evidence": evidence_id,
            "integrity": integrity_check_id,
        },
    )

    if custody is None:
        raise PaymentTaxAllocationError(
            "Matching encrypted custody required"
        )

    await retrieve_encrypted_document(
        db,
        company_id=company_id,
        evidence_id=evidence_id,
        actor_id=reviewed_by,
    )

    result = await record_independent_document_review(
        db,
        company_id=company_id,
        integrity_check_id=integrity_check_id,
        reviewed_by=reviewed_by,
        review_reference=review_reference,
        request_key=request_key,
    )

    return {
        **result,
        "authorized_company_review": True,
        "issuer_verified": False,
        "actual_tax_withholding_verified": False,
        "official_generation": "BLOCKED",
    }
