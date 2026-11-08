import base64
from copy import deepcopy
from datetime import date

import pytest

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
)
from cryptography.hazmat.primitives import serialization

from app.services.payroll_payment_tax_allocation_candidate import (
    PaymentTaxAllocationError,
)
from app.services.payroll_payment_tax_external_signature import (
    canonical_payload,
    verify_signed_source_candidate,
    verify_signed_source_batch,
)


def specimen():
    private = Ed25519PrivateKey.generate()

    public = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )

    payload = {
        "issuer_id": "test-issuer",
        "key_id": "test-key-v1",
        "receipt_id": "receipt-100",
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
    ).decode("ascii")

    return dict(
        payload=payload,
        signature_b64=signature,
        server_trusted_keys={
            ("test-issuer", "test-key-v1"): public,
        },
        expected_company_id=12,
        expected_payment_id=100,
        expected_component="personal_income_tax",
        expected_amount="180.00",
        expected_payment_date=date(2026, 9, 15),
    )


def test_signed_candidate_is_not_statutory_approval():
    result = verify_signed_source_candidate(**specimen())

    assert result["signature_cryptographically_valid"] is True
    assert result["bank_issuer_identity"] == "NOT_VERIFIED"
    assert result["actual_withholding"] == "NOT_VERIFIED"
    assert result["official_generation"] == "BLOCKED"


@pytest.mark.parametrize("mutation", [
    lambda x: x["payload"].update(amount="181.00"),
    lambda x: x["payload"].update(payment_id=101),
    lambda x: x["payload"].update(company_id=13),
    lambda x: x["payload"].update(component="military_levy"),
    lambda x: x["payload"].update(currency_code="EUR"),
    lambda x: x["payload"].update(source_date="2026-09-14"),
    lambda x: x.update(expected_payment_id=101),
    lambda x: x.update(expected_amount="181.00"),
    lambda x: x.update(signature_b64="not-base64"),
    lambda x: x.update(server_trusted_keys={}),
])
def test_invalid_signed_evidence_is_rejected(mutation):
    value = specimen()
    mutation(value)

    with pytest.raises(PaymentTaxAllocationError):
        verify_signed_source_candidate(**value)


def test_signature_from_unknown_key_is_rejected():
    value = specimen()

    another = Ed25519PrivateKey.generate()

    value["server_trusted_keys"] = {
        ("test-issuer", "test-key-v1"):
            another.public_key().public_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PublicFormat.Raw,
            ),
    }

    with pytest.raises(
        PaymentTaxAllocationError,
        match="signature verification failed",
    ):
        verify_signed_source_candidate(**value)


def test_replayed_receipt_in_batch_is_rejected():
    one = specimen()

    with pytest.raises(
        PaymentTaxAllocationError,
        match="Duplicate external receipt",
    ):
        verify_signed_source_batch(
            sources=[
                {
                    key: deepcopy(value)
                    for key, value in one.items()
                    if key != "server_trusted_keys"
                },
                {
                    key: deepcopy(value)
                    for key, value in one.items()
                    if key != "server_trusted_keys"
                },
            ],
            server_trusted_keys=one["server_trusted_keys"],
        )


def test_valid_batch_remains_blocked():
    one = specimen()

    result = verify_signed_source_batch(
        sources=[{
            key: value
            for key, value in one.items()
            if key != "server_trusted_keys"
        }],
        server_trusted_keys=one["server_trusted_keys"],
    )

    assert result["source_count"] == 1
    assert result["actual_withholding"] == "NOT_VERIFIED"
    assert result["official_generation"] == "BLOCKED"


def test_unapproved_payload_fields_are_rejected():
    one = specimen()
    one["payload"]["issuer_verified"] = True

    with pytest.raises(PaymentTaxAllocationError):
        verify_signed_source_candidate(**one)
