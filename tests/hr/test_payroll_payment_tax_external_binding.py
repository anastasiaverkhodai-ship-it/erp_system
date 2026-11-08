import base64
import json
from datetime import date

import pytest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
)

from app.services.payroll_payment_tax_allocation_candidate import (
    PaymentTaxAllocationError,
)
from app.services.payroll_payment_tax_external_signature import (
    canonical_payload,
)
import app.services.payroll_payment_tax_external_binding as module


def prepare(tmp_path, monkeypatch):
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )

    registry = {
        "version": 1,
        "keys": [{
            "issuer_id": "test-issuer",
            "key_id": "key-1",
            "public_key_b64": base64.b64encode(
                public
            ).decode(),
            "enabled": True,
        }],
    }

    file = tmp_path / "trusted.json"
    file.write_text(json.dumps(registry))
    file.chmod(0o600)

    monkeypatch.setenv(
        "ERP_PAYROLL_TRUSTED_ISSUER_KEYS_PATH",
        str(file),
    )

    return private, file, registry


def test_private_registry_loaded(tmp_path, monkeypatch):
    _, _, _ = prepare(tmp_path, monkeypatch)
    keys = module.load_server_trusted_keys()
    assert ("test-issuer", "key-1") in keys


def test_world_readable_registry_denied(
    tmp_path, monkeypatch
):
    _, file, _ = prepare(tmp_path, monkeypatch)
    file.chmod(0o644)

    with pytest.raises(PaymentTaxAllocationError):
        module.load_server_trusted_keys()


def test_duplicate_key_denied(tmp_path, monkeypatch):
    _, file, registry = prepare(
        tmp_path, monkeypatch
    )
    registry["keys"].append(dict(registry["keys"][0]))
    file.write_text(json.dumps(registry))

    with pytest.raises(
        PaymentTaxAllocationError,
        match="Duplicate",
    ):
        module.load_server_trusted_keys()


def test_disabled_key_not_accepted(
    tmp_path, monkeypatch
):
    _, file, registry = prepare(
        tmp_path, monkeypatch
    )
    registry["keys"][0]["enabled"] = False
    file.write_text(json.dumps(registry))

    assert module.load_server_trusted_keys() == {}


@pytest.mark.asyncio
async def test_db_bound_signature_remains_unapproved(
    tmp_path, monkeypatch
):
    private, _, _ = prepare(tmp_path, monkeypatch)

    async def audit(db, *, company_id, evidence_id):
        return {
            "official_generation": "BLOCKED",
            "independent_actual_withholding":
                "NOT_VERIFIED",
            "read_only": True,
            "payroll_disbursement_id": 100,
            "payment_specific_claim_binding": {
                "company_id": 12,
                "claim_count": 1,
                "official_generation": "BLOCKED",
                "payment_specific_withholding":
                    "NOT_VERIFIED",
                "claims": [{
                    "source_id": 200,
                    "payment_id": 100,
                    "component": "personal_income_tax",
                    "claimed_amount": "180.00",
                    "source_date": "2026-09-15",
                }],
            },
        }

    monkeypatch.setattr(
        module,
        "audit_payment_tax_sources",
        audit,
    )

    payload = {
        "issuer_id": "test-issuer",
        "key_id": "key-1",
        "receipt_id": "receipt-1",
        "company_id": 12,
        "payment_id": 100,
        "payment_date": "2026-09-15",
        "source_date": "2026-09-15",
        "component": "personal_income_tax",
        "amount": "180.00",
        "currency_code": "UAH",
    }

    signature = base64.b64encode(
        private.sign(canonical_payload(payload))
    ).decode()

    result = await module.verify_external_source_for_evidence(
        object(),
        company_id=12,
        evidence_id=200,
        signed_payload=payload,
        signature_b64=signature,
    )

    assert result["signature_cryptographically_valid"]
    assert result["issuer_identity"] == "NOT_VERIFIED"
    assert result["actual_withholding"] == "NOT_VERIFIED"
    assert result["official_generation"] == "BLOCKED"

    forged = dict(payload)
    forged["amount"] = "181.00"

    with pytest.raises(PaymentTaxAllocationError):
        await module.verify_external_source_for_evidence(
            object(),
            company_id=12,
            evidence_id=200,
            signed_payload=forged,
            signature_b64=signature,
        )
