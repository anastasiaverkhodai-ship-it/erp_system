"""Unit contracts for deterministic payroll source evidence."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.services.payroll_correction_source_evidence_service import (
    PayrollCorrectionEvidenceError,
    _canonical_value,
    _fingerprint,
)


def test_evidence_fingerprint_is_order_independent():
    first = {
        "amount": _canonical_value(Decimal("1200.00")),
        "date": _canonical_value(date(2026, 9, 1)),
        "minutes": 480,
    }

    second = {
        "minutes": 480,
        "date": _canonical_value(date(2026, 9, 1)),
        "amount": _canonical_value(Decimal("1200.00")),
    }

    assert _fingerprint(first) == _fingerprint(second)
    assert len(_fingerprint(first)) == 64


def test_evidence_detects_source_changes():
    baseline = {
        "worked_minutes": 480,
        "amount": _canonical_value(Decimal("1000.00")),
    }

    changed = {
        "worked_minutes": 600,
        "amount": _canonical_value(Decimal("1000.00")),
    }

    assert _fingerprint(baseline) != _fingerprint(changed)


def test_evidence_preserves_decimal_precision():
    first = _canonical_value(Decimal("1000.00"))
    second = _canonical_value(Decimal("1000.01"))

    assert first != second
    assert _fingerprint(first) != _fingerprint(second)


def test_evidence_handles_timestamp_and_rejects_unknown_types():
    timestamp = datetime(2026, 9, 1, 9, 0, tzinfo=UTC)

    assert _canonical_value(timestamp)["value"] == (
        "2026-09-01T09:00:00+00:00"
    )

    with pytest.raises(PayrollCorrectionEvidenceError):
        _canonical_value(object())

    with pytest.raises(PayrollCorrectionEvidenceError):
        _canonical_value(Decimal("NaN"))
