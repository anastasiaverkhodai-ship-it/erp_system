import pytest

from app.services.payroll_regulatory_unified_review import (
    UnifiedRegulatoryReviewError,
    validate_unified_review,
    validate_hr_snapshot_identity,
    require_identifier,
)


def review():
    return {
        "company_id": 1,
        "payroll_period_id": 2,
        "review_only": True,
        "official_generation": "BLOCKED",
        "reports": {
            kind: {
                "report_kind": kind,
                "company_id": 1,
                "payroll_period_id": 2,
                "review_only": True,
                "official_generation": "BLOCKED",
            }
            for kind in ("d1", "4df", "d5", "d6")
        },
    }


def test_complete_review_passes():
    assert validate_unified_review(review())


@pytest.mark.parametrize(
    "kind",
    ["d1", "4df", "d5", "d6"],
)
def test_missing_report_rejected(kind):
    data = review()
    del data["reports"][kind]

    with pytest.raises(UnifiedRegulatoryReviewError):
        validate_unified_review(data)


@pytest.mark.parametrize(
    "kind",
    ["d1", "4df", "d5", "d6"],
)
def test_cross_company_section_rejected(kind):
    data = review()
    data["reports"][kind]["company_id"] = 999

    with pytest.raises(UnifiedRegulatoryReviewError):
        validate_unified_review(data)


@pytest.mark.parametrize(
    "kind",
    ["d1", "4df", "d5", "d6"],
)
def test_cross_period_section_rejected(kind):
    data = review()
    data["reports"][kind]["payroll_period_id"] = 999

    with pytest.raises(UnifiedRegulatoryReviewError):
        validate_unified_review(data)


@pytest.mark.parametrize(
    "kind",
    ["d1", "4df", "d5", "d6"],
)
def test_unapproved_generation_rejected(kind):
    data = review()
    data["reports"][kind]["official_generation"] = "READY"

    with pytest.raises(UnifiedRegulatoryReviewError):
        validate_unified_review(data)


@pytest.mark.parametrize(
    "value",
    [0, -1, True, False, "1", None, 1.5],
)
def test_invalid_identifiers_rejected(value):
    with pytest.raises(UnifiedRegulatoryReviewError):
        require_identifier(value, "test")


def test_missing_snapshot_payload_rejected():
    with pytest.raises(UnifiedRegulatoryReviewError):
        validate_hr_snapshot_identity(
            {},
            company_id=1,
            payroll_period_id=2,
            report_kind="d5",
        )


def test_global_filing_guard_rejected():
    data = review()
    data["official_generation"] = "READY"

    with pytest.raises(UnifiedRegulatoryReviewError):
        validate_unified_review(data)
