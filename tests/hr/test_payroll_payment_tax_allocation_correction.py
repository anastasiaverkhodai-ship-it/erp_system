import pytest

from app.services.payroll_payment_tax_allocation_candidate import (
    PaymentTaxAllocationError,
)
from app.services.payroll_payment_tax_allocation_correction import (
    validate_transition,
)


@pytest.mark.parametrize(
    "latest, predecessor",
    [(None, None), (1, 1), (20, 20)],
)
def test_valid_chain(latest, predecessor):
    validate_transition(latest, predecessor)


@pytest.mark.parametrize(
    "latest, predecessor",
    [(None, 1), (1, None), (2, 1), (2, 3)],
)
def test_stale_or_invalid_chain(latest, predecessor):
    with pytest.raises(PaymentTaxAllocationError):
        validate_transition(latest, predecessor)
