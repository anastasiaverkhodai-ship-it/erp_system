import pytest

from app.services.payroll_payment_tax_allocation_candidate import (
    PaymentTaxAllocationError,
)
from app.services.payroll_payment_tax_evidence import (
    require_digest,
    version_key_for,
)


def test_original_version():
    assert version_key_for(None) == "original"


def test_correction_version():
    assert version_key_for(7) == "correction:7"


def test_valid_digest():
    value = "a" * 64
    assert require_digest(value) == value


@pytest.mark.parametrize(
    "value",
    ["", "A" * 64, "g" * 64, "a" * 63, 123, None],
)
def test_invalid_digest(value):
    with pytest.raises(PaymentTaxAllocationError):
        require_digest(value)


@pytest.mark.parametrize("value", [0, -1, True, "7"])
def test_invalid_correction_identity(value):
    with pytest.raises(PaymentTaxAllocationError):
        version_key_for(value)
