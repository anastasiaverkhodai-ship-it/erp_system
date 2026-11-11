"""Code-level D5 temporal guard regression.

Fixture approvals exist only inside monkeypatched tests.
No normative approval or official generation is granted.
"""

import hashlib
import json

import pytest

import app.services.payroll_d5_trusted_classifier as trusted


def entry(code, title, start, end=None):
    return {
        "profession_code": code,
        "professional_name": title,
        "effective_from": start,
        "effective_to": end,
    }


def load(monkeypatch, entries):
    raw = json.dumps({
        "source_reference": "TEST ONLY",
        "source_version": "TEST",
        "entries": entries,
    }, ensure_ascii=False).encode("utf-8")

    monkeypatch.setattr(
        trusted,
        "APPROVED_SOURCES",
        (trusted.ApprovedClassifierSource(
            sha256=hashlib.sha256(raw).hexdigest(),
            source_reference="TEST ONLY",
            source_version="TEST",
        ),),
    )

    return trusted.import_approved_classifier(raw)



def test_distinct_profession_names_can_overlap(monkeypatch):
    # The classifier identity is (code, professional_name).
    # Different names under the same code can coexist.
    result = load(monkeypatch, [
        entry("2419.2", "Old", "2010-11-01", "2016-04-01"),
        entry("2419.2", "New", "2016-03-01"),
    ])
    assert len(result) == 2




def test_distinct_profession_names_can_share_boundary(monkeypatch):
    result = load(monkeypatch, [
        entry("2419.2", "Old", "2010-11-01", "2016-03-01"),
        entry("2419.2", "New", "2016-03-01"),
    ])
    assert len(result) == 2



def test_nonoverlapping_titles_accepted(monkeypatch):
    result = load(monkeypatch, [
        entry("2419.2", "Old", "2010-11-01", "2016-02-29"),
        entry("2419.2", "New", "2016-03-01"),
    ])
    assert len(result) == 2


def test_different_codes_independent(monkeypatch):
    result = load(monkeypatch, [
        entry("2419.2", "A", "2016-03-01"),
        entry("1234", "B", "2016-03-01"),
    ])
    assert len(result) == 2


def test_unapproved_classifier_blocked(monkeypatch):
    monkeypatch.setattr(trusted, "APPROVED_SOURCES", ())

    raw = json.dumps({
        "source_reference": "TEST ONLY",
        "source_version": "TEST",
        "entries": [
            entry("2419.2", "A", "2016-03-01")
        ],
    }).encode()

    with pytest.raises(
        trusted.D5TrustedClassifierError,
        match="independent approval",
    ):
        trusted.import_approved_classifier(raw)


def test_same_identity_overlapping_intervals_rejected(monkeypatch):
    with pytest.raises(
        trusted.D5TrustedClassifierError,
        match="Overlapping classifier validity",
    ):
        load(monkeypatch, [
            entry("2419.2", "Same", "2016-01-01", "2016-06-30"),
            entry("2419.2", "Same", "2016-06-01", "2016-12-31"),
        ])


def test_same_identity_shared_boundary_rejected(monkeypatch):
    with pytest.raises(
        trusted.D5TrustedClassifierError,
        match="Overlapping classifier validity",
    ):
        load(monkeypatch, [
            entry("2419.2", "Same", "2016-01-01", "2016-06-30"),
            entry("2419.2", "Same", "2016-06-30"),
        ])


def test_same_identity_open_interval_rejected(monkeypatch):
    with pytest.raises(
        trusted.D5TrustedClassifierError,
        match="Overlapping classifier validity",
    ):
        load(monkeypatch, [
            entry("2419.2", "Same", "2016-01-01"),
            entry("2419.2", "Same", "2017-01-01"),
        ])


def test_same_identity_adjacent_nonoverlap_accepted(monkeypatch):
    result = load(monkeypatch, [
        entry("2419.2", "Same", "2016-01-01", "2016-06-29"),
        entry("2419.2", "Same", "2016-06-30"),
    ])
    assert len(result) == 2
