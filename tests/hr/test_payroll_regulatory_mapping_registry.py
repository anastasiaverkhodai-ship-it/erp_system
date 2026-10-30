from datetime import date
import pytest

import app.services.payroll_regulatory_mapping_registry as registry


def test_no_unreviewed_mappings_accepted():
    assert registry.approved_mappings_for(
        "d1",
        date(2026, 10, 1),
        date(2026, 10, 31),
    ) == {}


def test_independent_dated_mapping_lookup(monkeypatch):
    approved = registry.ApprovedRegulatoryMapping(
        mapping_id=17,
        report_kind="d1",
        effective_from=date(2026, 1, 1),
        effective_to=None,
        official_reference="TEST ONLY - NOT A LEGAL APPROVAL",
        source_sha256="a" * 64,
        approved=True,
    )

    monkeypatch.setattr(
        registry,
        "APPROVED_MAPPINGS",
        (approved,),
    )

    result = registry.approved_mappings_for(
        "d1",
        date(2026, 10, 1),
        date(2026, 10, 31),
    )

    assert set(result) == {17}

    assert registry.approved_mappings_for(
        "4df",
        date(2026, 10, 1),
        date(2026, 10, 31),
    ) == {}


def test_unapproved_entry_rejected(monkeypatch):
    item = registry.ApprovedRegulatoryMapping(
        mapping_id=18,
        report_kind="d1",
        effective_from=date(2026, 1, 1),
        effective_to=None,
        official_reference="TEST ONLY",
        source_sha256="b" * 64,
        approved=False,
    )

    monkeypatch.setattr(
        registry,
        "APPROVED_MAPPINGS",
        (item,),
    )

    assert registry.approved_mappings_for(
        "d1",
        date(2026, 10, 1),
        date(2026, 10, 31),
    ) == {}


def test_wrong_effective_date_rejected(monkeypatch):
    item = registry.ApprovedRegulatoryMapping(
        mapping_id=19,
        report_kind="d1",
        effective_from=date(2027, 1, 1),
        effective_to=None,
        official_reference="TEST ONLY",
        source_sha256="c" * 64,
        approved=True,
    )

    monkeypatch.setattr(
        registry,
        "APPROVED_MAPPINGS",
        (item,),
    )

    assert registry.approved_mappings_for(
        "d1",
        date(2026, 10, 1),
        date(2026, 10, 31),
    ) == {}


def test_duplicate_approved_mapping_rejected(monkeypatch):
    item = registry.ApprovedRegulatoryMapping(
        mapping_id=20,
        report_kind="d1",
        effective_from=date(2026, 1, 1),
        effective_to=None,
        official_reference="TEST ONLY",
        source_sha256="d" * 64,
        approved=True,
    )

    monkeypatch.setattr(
        registry,
        "APPROVED_MAPPINGS",
        (item, item),
    )

    with pytest.raises(
        registry.RegulatoryMappingError,
        match="Duplicate",
    ):
        registry.approved_mappings_for(
            "d1",
            date(2026, 10, 1),
            date(2026, 10, 31),
        )
