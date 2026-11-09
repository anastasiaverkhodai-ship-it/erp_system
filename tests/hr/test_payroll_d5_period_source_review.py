from dataclasses import replace
from datetime import date

import pytest

from app.services.payroll_d5_normative_acceptance import (
    D5NormativeEvidenceError,
    D5NormativeSourceCandidate,
    REQUIRED_SOURCE_KINDS,
    assess_d5_normative_candidates,
    review_d5_sources_for_period,
)
from app.services.payroll_d5_event_field_candidate import (
    D5EventFacts,
    D5FieldCandidateError,
    project_d5_event_candidate,
)


def source(kind, *, document_id=None,
           start=date(2026, 1, 1), end=None,
           url=None):
    document_id = document_id or f"source-{kind}"
    return D5NormativeSourceCandidate(
        document_id=document_id,
        source_kind=kind,
        source_url=url or f"https://example.org/{document_id}",
        content_sha256="a" * 64,
        effective_from=start,
        effective_to=end,
        review_reference="document section 1",
    )


def complete_sources():
    return [
        source(kind)
        for kind in sorted(REQUIRED_SOURCE_KINDS)
    ]


def test_five_source_kinds_cover_reporting_date():
    result = review_d5_sources_for_period(
        complete_sources(),
        date(2026, 11, 9),
    )

    assert result["missing_source_kinds"] == ()
    assert result["ambiguous_source_kinds"] == ()
    assert len(result["active_source_ids"]) == 5
    assert result["status"] == "REVIEW_REQUIRED"
    assert result["normative_mapping"] == "NOT_APPROVED"
    assert result["official_generation"] == "BLOCKED"
    assert result["official_rows_generated"] == 0


def test_expired_source_does_not_cover_period():
    sources = complete_sources()
    sources[0] = replace(
        sources[0],
        effective_to=date(2026, 10, 31),
    )

    result = review_d5_sources_for_period(
        sources, date(2026, 11, 9)
    )

    assert sources[0].source_kind in (
        result["missing_source_kinds"]
    )
    assert (
        "D5_REPORTING_PERIOD_SOURCE_COVERAGE_INCOMPLETE"
        in result["blockers"]
    )


def test_future_source_does_not_cover_period():
    sources = complete_sources()
    sources[0] = replace(
        sources[0],
        effective_from=date(2027, 1, 1),
    )

    result = review_d5_sources_for_period(
        sources, date(2026, 11, 9)
    )
    assert sources[0].source_kind in result["missing_source_kinds"]


def test_multiple_active_versions_require_review():
    sources = complete_sources()
    sources.append(
        source(
            sources[0].source_kind,
            document_id="alternative-source-version",
        )
    )

    result = review_d5_sources_for_period(
        sources, date(2026, 11, 9)
    )

    assert sources[0].source_kind in (
        result["ambiguous_source_kinds"]
    )
    assert (
        "D5_REPORTING_PERIOD_SOURCE_VERSION_AMBIGUOUS"
        in result["blockers"]
    )
    assert result["official_generation"] == "BLOCKED"


def test_distinct_versions_in_distinct_periods_not_ambiguous():
    sources = complete_sources()
    old = replace(
        sources[0],
        effective_to=date(2026, 6, 30),
    )
    new = source(
        sources[0].source_kind,
        document_id="new-effective-version",
        start=date(2026, 7, 1),
    )
    sources[0] = old
    sources.append(new)

    result = review_d5_sources_for_period(
        sources, date(2026, 11, 9)
    )

    assert result["ambiguous_source_kinds"] == ()
    assert result["missing_source_kinds"] == ()
    assert result["official_generation"] == "BLOCKED"


def test_changed_source_url_same_document_is_conflict():
    first = source("d5_filing_rules")
    second = replace(
        first,
        source_url="https://another.example.org/document",
    )

    result = assess_d5_normative_candidates(
        [first, second]
    )

    assert first.document_id in (
        result.conflicting_document_ids
    )
    assert "D5_NORMATIVE_SOURCE_CONFLICT" in result.blockers
    assert result.official_generation == "BLOCKED"


def test_exact_duplicate_source_rejected():
    item = source("d5_filing_rules")
    with pytest.raises(D5NormativeEvidenceError):
        assess_d5_normative_candidates([item, item])


@pytest.mark.parametrize(
    "bad_date",
    [None, "2026-11-09", True, 20261109],
)
def test_invalid_reporting_date_rejected(bad_date):
    with pytest.raises(D5NormativeEvidenceError):
        review_d5_sources_for_period(
            complete_sources(), bad_date
        )


def test_boolean_person_category_rejected():
    facts = D5EventFacts(
        company_id=1,
        employee_id=2,
        contract_id=3,
        employment_event_id=4,
        event_kind="hire",
        effective_date=date(2026, 11, 9),
        person_category=True,
        confirmed_identity="1234567890",
        full_name="Test Employee",
        profession_code="1234",
        profession_name="Test Profession",
        position_title="Test Position",
        order_reference="HR-001",
        source_sha256="a" * 64,
    )

    with pytest.raises(D5FieldCandidateError):
        project_d5_event_candidate(facts)
