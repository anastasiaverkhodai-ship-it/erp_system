"""Read-only binding of signed claims to actual payroll evidence.

Trust keys are loaded only from protected server configuration.
A key being configured is not independent issuer accreditation.
No source in this module can approve statutory generation.
"""

import base64
import binascii
import json
import os
import stat
from pathlib import Path

from app.services.payroll_payment_tax_allocation_candidate import (
    PaymentTaxAllocationError,
    require_id,
)
from app.services.payroll_payment_tax_external_signature import (
    verify_signed_source_candidate,
)
from app.services.payroll_payment_tax_independent_source_audit import (
    audit_payment_tax_sources,
)


def fail(message):
    raise PaymentTaxAllocationError(message)


def load_server_trusted_keys():
    """Read operator-provisioned Ed25519 public keys.

    ERP_PAYROLL_TRUSTED_ISSUER_KEYS_PATH points to a local JSON
    file. It must be a regular, private file, not a symlink.

    This loader never trusts keys from client-supplied payloads.
    """
    value = os.environ.get(
        "ERP_PAYROLL_TRUSTED_ISSUER_KEYS_PATH"
    )

    if not value:
        fail("Server issuer trust registry not configured")

    path = Path(value)

    flags = os.O_RDONLY
    flags |= getattr(os, "O_NOFOLLOW", 0)
    fd = None

    try:
        fd = os.open(path, flags)
        info = os.fstat(fd)

        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_mode & 0o077
            or info.st_size > 65536
        ):
            fail("Unsafe server trust registry permissions")

        with os.fdopen(fd, "rb") as stream:
            fd = None
            raw = stream.read(65537)

        if len(raw) > 65536:
            fail("Trust registry too large")

        content = json.loads(raw.decode("utf-8"))

    except (OSError, UnicodeError, ValueError) as exc:
        fail("Server trust registry unreadable")
    finally:
        if fd is not None:
            os.close(fd)

    if (
        type(content) is not dict
        or set(content) != {"version", "keys"}
        or content["version"] != 1
        or type(content["keys"]) is not list
        or not content["keys"]
    ):
        fail("Invalid server trust registry schema")

    trusted = {}

    for entry in content["keys"]:
        if (
            type(entry) is not dict
            or set(entry) != {
                "issuer_id",
                "key_id",
                "public_key_b64",
                "enabled",
            }
        ):
            fail("Invalid server issuer key entry")

        issuer = entry["issuer_id"]
        key_id = entry["key_id"]

        if (
            type(issuer) is not str
            or not issuer
            or len(issuer) > 200
            or type(key_id) is not str
            or not key_id
            or len(key_id) > 200
            or type(entry["enabled"]) is not bool
            or type(entry["public_key_b64"]) is not str
        ):
            fail("Invalid server issuer key identity")

        identity = (issuer, key_id)

        if identity in trusted:
            fail("Duplicate server issuer key")

        try:
            public = base64.b64decode(
                entry["public_key_b64"],
                validate=True,
            )
        except (ValueError, binascii.Error):
            fail("Invalid server public key encoding")

        if len(public) != 32:
            fail("Invalid server public key length")

        # Even disabled keys must not be silently duplicated.
        trusted[identity] = (
            public if entry["enabled"] else None
        )

    return {
        identity: public
        for identity, public in trusted.items()
        if public is not None
    }


async def verify_external_source_for_evidence(
    db,
    *,
    company_id,
    evidence_id,
    signed_payload,
    signature_b64,
):
    """Bind a signature check to persisted company payment evidence.

    This function deliberately does not accept expected amounts,
    payment IDs, tax components, or trusted public keys from the
    caller. These come from DB and protected server configuration.
    """
    company_id = require_id(company_id, "company")
    evidence_id = require_id(evidence_id, "evidence")

    source = await audit_payment_tax_sources(
        db,
        company_id=company_id,
        evidence_id=evidence_id,
    )

    if (
        source.get("official_generation") != "BLOCKED"
        or source.get("independent_actual_withholding")
            != "NOT_VERIFIED"
        or source.get("read_only") is not True
    ):
        fail("Unsafe payroll audit source state")

    binding = source.get(
        "payment_specific_claim_binding"
    )

    if (
        type(binding) is not dict
        or binding.get("company_id") != company_id
        or binding.get("claim_count") != 1
        or binding.get("official_generation") != "BLOCKED"
        or binding.get("payment_specific_withholding")
            != "NOT_VERIFIED"
    ):
        fail("Verified database claim binding required")

    claims = binding.get("claims")

    if type(claims) is not list or len(claims) != 1:
        fail("Single database claim required")

    claim = claims[0]

    if (
        claim.get("source_id") != evidence_id
        or claim.get("payment_id")
            != source.get("payroll_disbursement_id")
    ):
        fail("Payment evidence identity mismatch")

    from datetime import date

    try:
        payment_date = date.fromisoformat(
            claim["source_date"]
        )
    except (KeyError, TypeError, ValueError):
        fail("Invalid database payment date")

    trusted = load_server_trusted_keys()

    verified = verify_signed_source_candidate(
        payload=signed_payload,
        signature_b64=signature_b64,
        server_trusted_keys=trusted,
        expected_company_id=company_id,
        expected_payment_id=claim["payment_id"],
        expected_component=claim["component"],
        expected_amount=claim["claimed_amount"],
        expected_payment_date=payment_date,
    )

    if (
        verified["official_generation"] != "BLOCKED"
        or verified["actual_withholding"] != "NOT_VERIFIED"
    ):
        fail("External candidate cannot grant approval")

    return {
        "company_id": company_id,
        "evidence_id": evidence_id,
        "payment_id": claim["payment_id"],
        "source_component": claim["component"],
        "signature_cryptographically_valid": True,
        "server_configured_key_match": True,
        "issuer_identity": "NOT_VERIFIED",
        "receipt_replay_across_database": "NOT_VERIFIED",
        "actual_withholding": "NOT_VERIFIED",
        "gross_paid_attribution": "NOT_APPROVED",
        "read_only": True,
        "official_generation": "BLOCKED",
    }
