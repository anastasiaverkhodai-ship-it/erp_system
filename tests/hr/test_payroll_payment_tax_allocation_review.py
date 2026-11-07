import pytest

from app.services.payroll_payment_tax_allocation_candidate import (
    PaymentTaxAllocationError,
)
from app.services.payroll_payment_tax_allocation_review import (
    resolve_event_state,
)


@pytest.mark.parametrize(
    "events, expected",
    [
        ([], "draft"),
        (["reviewed"], "reviewed"),
        (["revoked"], "revoked"),
        (["reviewed", "revoked"], "revoked"),
    ],
)
def test_valid_history(events, expected):
    assert resolve_event_state(events) == expected


@pytest.mark.parametrize(
    "events",
    [
        ["unknown"],
        ["reviewed", "reviewed"],
        ["revoked", "reviewed"],
        ["revoked", "revoked"],
        ["reviewed", "revoked", "reviewed"],
    ],
)
def test_invalid_history_rejected(events):
    with pytest.raises(PaymentTaxAllocationError):
        resolve_event_state(events)
