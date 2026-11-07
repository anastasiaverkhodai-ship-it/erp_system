from datetime import date
from types import SimpleNamespace

import pytest

from app.services.payroll_d5_database_correction_chain import (
    D5CorrectionChainError,
    _id,
    _validate_link,
)


def records():
    shared = {
        "company_id": 1,
        "employment_contract_id": 2,
        "effective_date": date(2026, 10, 15),
    }
    original = SimpleNamespace(
        **shared,
        id=3,
        event_type="hire",
        order_number="ORDER-A",
    )
    reversal = SimpleNamespace(
        **shared,
        id=4,
        event_type="reversal",
        reversal_of_id=3,
    )
    replacement = SimpleNamespace(
        **shared,
        id=5,
        event_type="hire",
        order_number="ORDER-B",
        request_payload={
            "replaces_employment_event_id": 3
        },
    )
    return original, reversal, replacement


def test_valid_explicit_link():
    _validate_link(*records())


def test_missing_replacement_link_rejected():
    a, b, c = records()
    c.request_payload = {}
    with pytest.raises(
        D5CorrectionChainError,
        match="Explicit persisted",
    ):
        _validate_link(a, b, c)


def test_wrong_reversal_rejected():
    a, b, c = records()
    b.reversal_of_id = 999
    with pytest.raises(
        D5CorrectionChainError,
        match="Invalid original",
    ):
        _validate_link(a, b, c)


def test_different_company_rejected():
    a, b, c = records()
    c.company_id = 9
    with pytest.raises(
        D5CorrectionChainError,
        match="Cross-company",
    ):
        _validate_link(a, b, c)


def test_different_reporting_date_rejected():
    a, b, c = records()
    c.effective_date = date(2026, 11, 1)
    with pytest.raises(
        D5CorrectionChainError,
        match="Different-period",
    ):
        _validate_link(a, b, c)


def test_termination_requires_separate_review():
    a, b, c = records()
    a.event_type = "termination"
    c.event_type = "termination"
    with pytest.raises(
        D5CorrectionChainError,
        match="separate correction",
    ):
        _validate_link(a, b, c)


@pytest.mark.parametrize(
    "value", [0, -1, True, None, "5"]
)
def test_invalid_ids(value):
    with pytest.raises(D5CorrectionChainError):
        _id(value, "event_id")


def test_unique_replacement_link():
    from app.services.payroll_d5_database_correction_chain import (
        _require_unique_replacement,
    )

    _, _, replacement = records()

    assert _require_unique_replacement(
        [replacement],
        original_event_id=3,
        expected_replacement_id=5,
    ) is True


def test_duplicate_replacement_links_rejected():
    from app.services.payroll_d5_database_correction_chain import (
        _require_unique_replacement,
    )

    _, _, replacement = records()
    other = SimpleNamespace(
        id=6,
        request_payload={
            "replaces_employment_event_id": 3,
        },
    )

    with pytest.raises(
        D5CorrectionChainError,
        match="Unique active replacement link",
    ):
        _require_unique_replacement(
            [replacement, other],
            original_event_id=3,
            expected_replacement_id=5,
        )


def test_boolean_replacement_link_not_accepted():
    from app.services.payroll_d5_database_correction_chain import (
        _require_unique_replacement,
    )

    _, _, replacement = records()
    replacement.request_payload = {
        "replaces_employment_event_id": True,
    }

    with pytest.raises(
        D5CorrectionChainError,
        match="Unique active replacement link",
    ):
        _require_unique_replacement(
            [replacement],
            original_event_id=1,
            expected_replacement_id=5,
        )
