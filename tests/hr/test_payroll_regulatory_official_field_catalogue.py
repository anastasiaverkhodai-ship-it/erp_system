from dataclasses import replace
from datetime import date

import pytest

from app.services.payroll_regulatory_official_field_catalogue import (
    FIELD_CANDIDATES,
    OfficialFieldCatalogueError,
    catalogue_for_form,
    field_review_status,
    validate_catalogue,
    verify_catalogue_not_authoritative,
)


def test_four_forms_present():
    assert {
        row.report_kind for row in FIELD_CANDIDATES
    } == {"d1", "4df", "d5", "d6"}


def test_four_df_source_anchored_fields():
    fields = {
        row.field_key
        for row in catalogue_for_form("4df")
    }

    assert {
        "section_i_column_2",
        "section_i_column_3a",
        "section_i_column_3",
        "section_i_column_4a",
        "section_i_column_6",
    } <= fields


def test_d5_and_d6_not_assumed_universally_applicable():
    d5 = catalogue_for_form("d5")
    d6 = catalogue_for_form("d6")

    assert any(
        "applicability" in row.field_key for row in d5
    )
    assert any(
        "applicability" in row.field_key for row in d6
    )


def test_source_links_are_explicit():
    validate_catalogue()

    assert all(
        row.source_url.startswith(
            "https://zakon.rada.gov.ua/"
        )
        for row in FIELD_CANDIDATES
    )


@pytest.mark.parametrize("kind", ["d1", "4df", "d5", "d6"])
def test_candidate_cannot_authorize_generation(kind):
    status = field_review_status(
        period_start=date(2026, 10, 1),
        period_end=date(2026, 10, 31),
    )["forms"][kind]

    assert status["official_generation"] == "BLOCKED"
    assert status["field_mapping_approved"] == 0
    assert status["document_artifacts_verified"] == 3
    assert not status["complete_form_schema_verified"]
    assert not status["effective_date_coverage_verified"]


def test_candidate_cannot_be_marked_approved():
    changed = replace(
        FIELD_CANDIDATES[0],
        status="APPROVED",
    )

    with pytest.raises(
        OfficialFieldCatalogueError,
        match="cannot approve",
    ):
        validate_catalogue([changed])


def test_duplicate_candidate_rejected():
    with pytest.raises(
        OfficialFieldCatalogueError,
        match="Duplicate",
    ):
        validate_catalogue([
            FIELD_CANDIDATES[0],
            FIELD_CANDIDATES[0],
        ])


def test_unknown_source_rejected():
    changed = replace(
        FIELD_CANDIDATES[0],
        source_url="https://example.org/unknown",
    )

    with pytest.raises(
        OfficialFieldCatalogueError,
        match="Unrecognized",
    ):
        validate_catalogue([changed])


def test_invalid_date_range_rejected():
    with pytest.raises(
        OfficialFieldCatalogueError,
        match="Invalid reporting period",
    ):
        field_review_status(
            period_start=date(2026, 11, 1),
            period_end=date(2026, 10, 1),
        )


def test_unknown_form_rejected():
    with pytest.raises(
        OfficialFieldCatalogueError,
        match="Unsupported",
    ):
        catalogue_for_form("unsupported")


def test_production_mapping_registry_remains_separate():
    assert verify_catalogue_not_authoritative()


def test_all_candidates_are_unapproved():
    assert all(
        row.status == "CANDIDATE"
        for row in FIELD_CANDIDATES
    )


def test_changed_field_contract_reports_unavailable_without_authorizing(monkeypatch):
    from app.services import payroll_regulatory_form_fields as fields

    monkeypatch.setattr(fields, "CONTRACT_SHA256", "0" * 64)
    status = field_review_status(
        period_start=date(2026, 10, 1), period_end=date(2026, 10, 31),
    )
    for form in status["forms"].values():
        assert form["verified_field_locations"] == 0
        assert form["field_location_source"] == "UNAVAILABLE_OR_CHANGED"
        assert form["official_generation"] == "BLOCKED"
