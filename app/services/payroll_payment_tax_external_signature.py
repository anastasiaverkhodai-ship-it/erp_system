"""Fail-closed cryptographic checks for payment tax evidence.

A valid signature means only that an approved local public key
matches the signed bytes. Bank identity, settlement, actual
withholding and statutory readiness remain NOT_VERIFIED.

The trusted key registry must be configured server-side.
Never accept the registry from an HTTP request or document.
"""

import base64
import binascii
import json
from datetime import date
from decimal import Decimal, InvalidOperation

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PublicKey,
)

from app.services.payroll_payment_tax_allocation_candidate import (
    PaymentTaxAllocationError,
)


FIELDS = frozenset({
    "issuer_id",
    "key_id",
    "receipt_id",
    "company_id",
    "payment_id",
    "payment_date",
    "source_date",
    "component",
    "amount",
    "currency_code",
})

COMPONENTS = frozenset({
    "personal_income_tax",
    "military_levy",
})


def _fail(message):
    raise PaymentTaxAllocationError(message)


def _integer(value):
    if type(value) is not int or value <= 0:
        _fail("Invalid signed source identity")
    return value


def _string(value, limit=200):
    if (
        type(value) is not str
        or not value
        or value != value.strip()
        or len(value) > limit
    ):
        _fail("Invalid signed source text")
    return value


def _date(value):
    if type(value) is not str:
        _fail("Invalid signed source date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        _fail("Invalid signed source date")
    if parsed.isoformat() != value:
        _fail("Noncanonical signed source date")
    return parsed


def _money(value):
    if type(value) is not str:
        _fail("Invalid signed source amount")
    try:
        amount = Decimal(value)
    except (InvalidOperation, ValueError):
        _fail("Invalid signed source amount")

    if (
        not amount.is_finite()
        or amount <= 0
        or amount != amount.quantize(Decimal("0.01"))
        or str(amount) != value
    ):
        _fail("Noncanonical signed source amount")

    return amount


def _decode64(value, expected):
    if type(value) is not str:
        _fail("Invalid signature encoding")
    try:
        raw = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error):
        _fail("Invalid signature encoding")

    if len(raw) != expected:
        _fail("Invalid signature or key length")
    return raw


def canonical_payload(payload):
    """Canonical application-defined envelope, not a bank format."""
    if type(payload) is not dict or set(payload) != FIELDS:
        _fail("Invalid signed payload schema")

    _string(payload["issuer_id"])
    _string(payload["key_id"])
    _string(payload["receipt_id"])

    _integer(payload["company_id"])
    _integer(payload["payment_id"])

    paid = _date(payload["payment_date"])
    sourced = _date(payload["source_date"])

    if sourced < paid:
        _fail("Evidence predates payment")

    if payload["component"] not in COMPONENTS:
        _fail("Unsupported signed tax component")

    if payload["currency_code"] != "UAH":
        _fail("Invalid signed currency")

    _money(payload["amount"])

    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def verify_signed_source_candidate(
    *,
    payload,
    signature_b64,
    server_trusted_keys,
    expected_company_id,
    expected_payment_id,
    expected_component,
    expected_amount,
    expected_payment_date,
):
    """Check one candidate; never grant issuer/statutory approval.

    server_trusted_keys maps (issuer_id, key_id) to raw 32-byte
    Ed25519 public keys, provisioned outside the evidence flow.
    """
    signed = canonical_payload(payload)

    if type(server_trusted_keys) is not dict:
        _fail("Server trust registry unavailable")

    company = _integer(expected_company_id)
    payment = _integer(expected_payment_id)

    if (
        payload["company_id"] != company
        or payload["payment_id"] != payment
        or payload["component"] != expected_component
        or _money(payload["amount"])
            != _money(expected_amount)
        or _date(payload["payment_date"])
            != expected_payment_date
    ):
        _fail("External source does not match payroll payment")

    key = server_trusted_keys.get((
        payload["issuer_id"],
        payload["key_id"],
    ))

    if type(key) is not bytes or len(key) != 32:
        _fail("Issuer key is not registered")

    signature = _decode64(signature_b64, 64)

    try:
        Ed25519PublicKey.from_public_bytes(
            key
        ).verify(signature, signed)
    except (InvalidSignature, ValueError):
        _fail("External signature verification failed")

    return {
        "receipt_id": payload["receipt_id"],
        "issuer_id": payload["issuer_id"],
        "key_id": payload["key_id"],
        "company_id": company,
        "payment_id": payment,
        "component": expected_component,
        "amount": payload["amount"],
        "signature_cryptographically_valid": True,
        "bank_issuer_identity": "NOT_VERIFIED",
        "external_settlement": "NOT_VERIFIED",
        "actual_withholding": "NOT_VERIFIED",
        "official_generation": "BLOCKED",
    }


def verify_signed_source_batch(*, sources, server_trusted_keys):
    """Reject repeated receipt identities within a candidate batch.

    Each source supplies payload, signature_b64, and expected
    payment fields from trusted server-side records.
    """
    if type(sources) is not list or not sources:
        _fail("Signed source batch required")

    used = set()
    results = []

    for source in sources:
        if type(source) is not dict:
            _fail("Invalid signed source batch row")

        result = verify_signed_source_candidate(
            server_trusted_keys=server_trusted_keys,
            **source,
        )

        identity = (
            result["issuer_id"],
            result["receipt_id"],
        )

        if identity in used:
            _fail("Duplicate external receipt identity")

        used.add(identity)
        results.append(result)

    return {
        "source_count": len(results),
        "sources": results,
        "external_issuer_acceptance": "NOT_VERIFIED",
        "actual_withholding": "NOT_VERIFIED",
        "official_generation": "BLOCKED",
    }
