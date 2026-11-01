from copy import deepcopy
from datetime import date
from decimal import Decimal

import pytest

from app.services.payroll_regulatory_correction_review import (
    RegulatoryCorrectionReviewError,
    canonical,
    compare_regulatory_snapshots,
    normalize,
    snapshot_regulatory_review,
    validate_review_snapshot,
)


HASH = "a" * 64


def make_review():
    return {
        "company_id": 1,
        "payroll_period_id": 2,
        "review_only": True,
        "official_generation": "BLOCKED",
        "reports": {
            "d1": {
                "report_kind": "d1",
                "company_id": 1,
                "payroll_period_id": 2,
                "review_only": True,
                "official_generation": "BLOCKED",
                "source_readiness": {
                    "amount": Decimal("125.50"),
                    "date": date(2026, 10, 31),
                },
            },
            "4df": {
                "report_kind": "4df",
                "company_id": 1,
                "payroll_period_id": 2,
                "review_only": True,
                "official_generation": "BLOCKED",
                "source_readiness": {"records": 1},
            },
            "d5": {
                "report_kind": "d5",
                "company_id": 1,
                "payroll_period_id": 2,
                "review_only": True,
                "official_generation": "BLOCKED",
                "statutory_mapping": "NOT_APPROVED",
                "hr_source_snapshot_sha256": HASH,
            },
            "d6": {
                "report_kind": "d6",
                "company_id": 1,
                "payroll_period_id": 2,
                "review_only": True,
                "official_generation": "BLOCKED",
                "statutory_mapping": "NOT_APPROVED",
                "applicability": "NOT_DETERMINED",
                "hr_source_snapshot_sha256": HASH,
            },
        },
    }


def test_deterministic_snapshot():
    first = snapshot_regulatory_review(make_review())
    second = snapshot_regulatory_review(make_review())
    assert first == second
    assert validate_review_snapshot(first)


def test_no_changes():
    first = snapshot_regulatory_review(make_review())
    result = compare_regulatory_snapshots(first, deepcopy(first))
    assert result["changed_report_count"] == 0
    assert result["statutory_correction_generated"] is False


@pytest.mark.parametrize("kind", ["d1", "4df", "d5", "d6"])
def test_each_report_change_detected(kind):
    old = make_review()
    new = deepcopy(old)

    if kind in ("d1", "4df"):
        new["reports"][kind]["source_readiness"]["extra_source"] = 5
    else:
        new["reports"][kind][
            "hr_source_snapshot_sha256"
        ] = "b" * 64

    comparison = compare_regulatory_snapshots(
        snapshot_regulatory_review(old),
        snapshot_regulatory_review(new),
    )

    assert comparison["changed_report_count"] == 1
    assert comparison["changed_reports"][0]["report_kind"] == kind
    assert comparison["official_generation"] == "BLOCKED"


@pytest.mark.parametrize("field", ["company_id", "payroll_period_id"])
def test_cross_scope_comparison_rejected(field):
    first = snapshot_regulatory_review(make_review())
    second = deepcopy(first)
    second[field] += 1
    second["overall_sha256"] = __import__(
        "app.services.payroll_regulatory_correction_review",
        fromlist=["digest"],
    ).digest({
        "company_id": second["company_id"],
        "payroll_period_id": second["payroll_period_id"],
        "sections": second["section_digests"],
    })

    with pytest.raises(RegulatoryCorrectionReviewError):
        compare_regulatory_snapshots(first, second)


def test_tampered_snapshot_rejected():
    snapshot = snapshot_regulatory_review(make_review())
    snapshot["section_digests"]["d1"] = "b" * 64

    with pytest.raises(RegulatoryCorrectionReviewError):
        validate_review_snapshot(snapshot)


def test_unapproved_generation_rejected():
    review = make_review()
    review["reports"]["d1"]["official_generation"] = "READY"

    with pytest.raises(Exception):
        snapshot_regulatory_review(review)


def test_unverified_d6_status_rejected():
    review = make_review()
    review["reports"]["d6"]["applicability"] = "APPLICABLE"

    with pytest.raises(RegulatoryCorrectionReviewError):
        snapshot_regulatory_review(review)


@pytest.mark.parametrize(
    "value",
    [float("nan"), float("inf"), Decimal("NaN"), object(), {1: "x"}],
)
def test_unsupported_source_values_rejected(value):
    with pytest.raises(RegulatoryCorrectionReviewError):
        canonical({"value": value})


def test_financial_decimal_precision():
    assert canonical(Decimal("1.20")) != canonical(Decimal("1.2"))


def test_empty_missing_section_rejected():
    review = make_review()
    del review["reports"]["4df"]

    with pytest.raises(Exception):
        snapshot_regulatory_review(review)
