from datetime import date

import pytest

from app.services.payroll_regulatory_form_version_gate import (
    FormVersionError,
    VersionDecision,
    assess_form_version,
    assess_package,
)


@pytest.mark.parametrize("kind", ["d1", "4df", "d5", "d6"])
def test_first_reporting_month(kind):
    result = assess_form_version(
        report_kind=kind,
        report_month=date(2026, 7, 1),
        submission_date=date(2026, 8, 1),
    )
    assert result.decision == VersionDecision.NEW_2026_CANDIDATE
    assert result.official_generation == "BLOCKED"


@pytest.mark.parametrize("kind", ["d1", "4df", "d5", "d6"])
def test_previous_reporting_month_needs_review(kind):
    result = assess_form_version(
        report_kind=kind,
        report_month=date(2026, 6, 1),
        submission_date=date(2026, 8, 1),
    )
    assert result.decision == VersionDecision.HISTORICAL_REVIEW


def test_october_2026_regular_report():
    result = assess_form_version(
        report_kind="4df",
        report_month=date(2026, 10, 1),
        submission_date=date(2026, 11, 2),
    )
    assert result.decision == VersionDecision.NEW_2026_CANDIDATE


@pytest.mark.parametrize("submission_type", ["new", "correction"])
def test_correction_requires_separate_mapping(submission_type):
    result = assess_form_version(
        report_kind="4df",
        report_month=date(2026, 7, 1),
        submission_date=date(2026, 10, 1),
        submission_type=submission_type,
    )
    assert result.decision == VersionDecision.CORRECTION_REVIEW


def test_no_historical_inference_for_pre_effective_submission():
    result = assess_form_version(
        report_kind="d1",
        report_month=date(2026, 7, 1),
        submission_date=date(2026, 7, 10),
    )
    assert result.decision == VersionDecision.HISTORICAL_REVIEW


def test_non_legal_entity_rejected():
    with pytest.raises(FormVersionError, match="legal entities"):
        assess_form_version(
            report_kind="d1",
            report_month=date(2026, 7, 1),
            submission_date=date(2026, 8, 1),
            legal_entity=False,
        )


def test_bad_report_month_rejected():
    with pytest.raises(FormVersionError):
        assess_form_version(
            report_kind="d1",
            report_month=date(2026, 7, 15),
            submission_date=date(2026, 8, 1),
        )


def test_bad_submission_type_rejected():
    with pytest.raises(FormVersionError):
        assess_form_version(
            report_kind="d1",
            report_month=date(2026, 7, 1),
            submission_date=date(2026, 8, 1),
            submission_type="unknown",
        )


def test_d5_d6_not_unconditionally_required():
    result = assess_package(
        report_month=date(2026, 10, 1),
        submission_date=date(2026, 11, 2),
    )
    assert result["d5_applicability"] == "REQUIRES_EVENT_EVIDENCE"
    assert result["d6_applicability"] == "REQUIRES_SPECIAL_SERVICE_EVIDENCE"
    assert result["official_generation"] == "BLOCKED"
    assert not result["mapping_approved"]


def test_all_forms_remain_blocked():
    result = assess_package(
        report_month=date(2026, 10, 1),
        submission_date=date(2026, 11, 2),
    )
    assert len(result["forms"]) == 4
    assert all(
        item.official_generation == "BLOCKED"
        for item in result["forms"].values()
    )
