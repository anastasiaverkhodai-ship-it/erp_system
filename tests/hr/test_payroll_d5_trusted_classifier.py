
"""Synthetic trust-anchor tests; no assertion of official approval."""
import hashlib
import json
from dataclasses import replace

import pytest

import app.services.payroll_d5_trusted_classifier as registry
from app.services.payroll_d5_trusted_classifier import (
    ApprovedClassifierSource,
    D5TrustedClassifierError,
    import_approved_classifier,
)


def sample(entries=None):
    if entries is None:
        entries = [{
            "profession_code": "1234",
            "professional_name": "Synthetic Profession",
            "effective_from": "2026-01-01",
            "effective_to": None,
        }]
    return json.dumps({
        "source_reference": "synthetic-source",
        "source_version": "synthetic-v1",
        "entries": entries,
    }, sort_keys=True).encode("utf-8")


def approve_in_test(monkeypatch, raw):
    digest = hashlib.sha256(raw).hexdigest()
    monkeypatch.setattr(registry, "APPROVED_SOURCES", (
        ApprovedClassifierSource(
            sha256=digest,
            source_reference="synthetic-source",
            source_version="synthetic-v1",
        ),
    ))


def test_unapproved_source_is_rejected():
    with pytest.raises(D5TrustedClassifierError):
        import_approved_classifier(sample())


def test_approved_synthetic_source(monkeypatch):
    raw = sample()
    approve_in_test(monkeypatch, raw)
    result = import_approved_classifier(raw)
    assert len(result) == 1
    assert result[0].profession_code == "1234"
    assert result[0].reviewed is True


def test_modified_bytes_rejected(monkeypatch):
    raw = sample()
    approve_in_test(monkeypatch, raw)
    with pytest.raises(D5TrustedClassifierError):
        import_approved_classifier(raw + b" ")


def test_duplicate_entry_rejected(monkeypatch):
    entry = {
        "profession_code": "1234",
        "professional_name": "Synthetic Profession",
        "effective_from": "2026-01-01",
        "effective_to": None,
    }
    raw = sample([entry, entry])
    approve_in_test(monkeypatch, raw)
    with pytest.raises(D5TrustedClassifierError, match="Duplicate"):
        import_approved_classifier(raw)


def test_overlapping_history_rejected(monkeypatch):
    first = {
        "profession_code": "1234",
        "professional_name": "Synthetic Profession",
        "effective_from": "2026-01-01",
        "effective_to": "2026-08-31",
    }
    second = {
        **first,
        "effective_from": "2026-08-01",
        "effective_to": None,
    }
    raw = sample([first, second])
    approve_in_test(monkeypatch, raw)
    with pytest.raises(D5TrustedClassifierError, match="Overlapping"):
        import_approved_classifier(raw)


def test_invalid_code_rejected(monkeypatch):
    raw = sample([{
        "profession_code": "TEST",
        "professional_name": "Synthetic Profession",
        "effective_from": "2026-01-01",
        "effective_to": None,
    }])
    approve_in_test(monkeypatch, raw)
    with pytest.raises(D5TrustedClassifierError):
        import_approved_classifier(raw)


def test_approval_identity_mismatch_rejected(monkeypatch):
    raw = sample()
    approve_in_test(monkeypatch, raw)
    approval = registry.APPROVED_SOURCES[0]
    monkeypatch.setattr(registry, "APPROVED_SOURCES", (
        replace(approval, source_version="wrong-version"),
    ))
    with pytest.raises(D5TrustedClassifierError):
        import_approved_classifier(raw)


def test_trusted_import_accepts_dotted_code(monkeypatch):
    raw = sample([{
        "profession_code": "2419.2",
        "professional_name": "Synthetic Dotted Profession",
        "effective_from": "2026-01-01",
        "effective_to": None,
    }])
    approve_in_test(monkeypatch, raw)
    result = import_approved_classifier(raw)
    assert result[0].profession_code == "2419.2"


@pytest.mark.parametrize(
    "bad_code",
    ["2419.", "2419.22", "24.19", "ABCD", "２４１９"],
)
def test_trusted_import_rejects_invalid_code_shapes(
    monkeypatch, bad_code
):
    raw = sample([{
        "profession_code": bad_code,
        "professional_name": "Synthetic Profession",
        "effective_from": "2026-01-01",
        "effective_to": None,
    }])
    approve_in_test(monkeypatch, raw)
    with pytest.raises(D5TrustedClassifierError):
        import_approved_classifier(raw)
