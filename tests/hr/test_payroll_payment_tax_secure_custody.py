import base64
import os

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.services.payroll_payment_tax_allocation_candidate import (
    PaymentTaxAllocationError,
)
from app.services.payroll_payment_tax_secure_custody import (
    _aad,
    _key,
)


def test_key_is_required(monkeypatch):
    monkeypatch.delenv("ERP_PAYROLL_CUSTODY_KEY_B64", raising=False)
    monkeypatch.delenv("ERP_PAYROLL_CUSTODY_KEY_ID", raising=False)

    with pytest.raises(PaymentTaxAllocationError):
        _key()


def test_256_bit_key(monkeypatch):
    key = AESGCM.generate_key(bit_length=256)

    monkeypatch.setenv(
        "ERP_PAYROLL_CUSTODY_KEY_B64",
        base64.b64encode(key).decode(),
    )
    monkeypatch.setenv(
        "ERP_PAYROLL_CUSTODY_KEY_ID", "test-key-1"
    )

    assert _key() == ("test-key-1", key)


@pytest.mark.parametrize(
    "value",
    ["", "not-base64!!!", base64.b64encode(b"short").decode()],
)
def test_bad_key_fails_closed(monkeypatch, value):
    monkeypatch.setenv(
        "ERP_PAYROLL_CUSTODY_KEY_B64", value
    )
    monkeypatch.setenv(
        "ERP_PAYROLL_CUSTODY_KEY_ID", "test-key-1"
    )

    with pytest.raises(PaymentTaxAllocationError):
        _key()


def test_authenticated_company_binding():
    key = AESGCM.generate_key(bit_length=256)
    nonce = os.urandom(12)
    digest = "a" * 64
    document = b"encrypted test payroll evidence"

    encrypted = AESGCM(key).encrypt(
        nonce, document, _aad(1, 10, digest)
    )

    assert AESGCM(key).decrypt(
        nonce, encrypted, _aad(1, 10, digest)
    ) == document

    with pytest.raises(Exception):
        AESGCM(key).decrypt(
            nonce, encrypted, _aad(2, 10, digest)
        )
