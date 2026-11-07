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
