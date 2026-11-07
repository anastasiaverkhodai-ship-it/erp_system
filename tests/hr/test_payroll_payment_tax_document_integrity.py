import hashlib

import pytest

from app.services.payroll_payment_tax_allocation_candidate import (
    PaymentTaxAllocationError,
)
from app.services.payroll_payment_tax_document_integrity import (
    document_fingerprint,
)


def test_real_document_digest():
    content = b"independent payroll document test"

    digest, size = document_fingerprint(content)

    assert digest == hashlib.sha256(content).hexdigest()
    assert size == len(content)


@pytest.mark.parametrize(
    "content",
    [
        b"",
        None,
        "document",
        bytearray(b"abc"),
        b"a" * 5242881,
    ],
)
def test_invalid_document_bytes(content):
    with pytest.raises(PaymentTaxAllocationError):
        document_fingerprint(content)
