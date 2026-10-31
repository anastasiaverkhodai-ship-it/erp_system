from copy import deepcopy

import pytest

from app.services.payroll_regulatory_d5_d6_workflow import (
    D5D6WorkflowError,
    assemble_hr_review,
    compare_hr_reviews,
    verify_review_snapshot,
)


HASH_A = "a" * 64
HASH_B = "b" * 64


def inventory():
    return {
        "company_id": 1,
        "payroll_period_id": 2,
        "period_start": "2026-10-01",
        "period_end": "2026-10-31",
        "review_only": True,
        "official_statutory_generation": "BLOCKED",
        "dated_evidence_count": 1,
        "ambiguous_evidence_count": 0,
        "missing_source_tables": [
            "employment_contracts",
            "payroll_d5_professional_evidence",
        ],
        "source_inventory": [{
            "source_table": "hr_changes",
            "scanned_count": 1,
            "dated_in_period_count": 1,
            "undated_or_ambiguous_count": 0,
            "dated_outside_period_count": 0,
            "dated_evidence": [{
                "source_table": "hr_changes",
                "source_id": 5,
                "source_sha256": HASH_A,
                "company_id": 1,
                "date_field": "event_date",
                "event_date": "2026-10-15",
                "statutory_classification": "NOT_VERIFIED",
                "review_only": True,
            }],
            "undated_evidence": [],
        }],
    }


def test_positive_workflow():
    result = assemble_hr_review(inventory())
    assert verify_review_snapshot(result)
    assert result["d5"]["candidate_event_count"] == 1
    assert result["d6"]["applicability"] == "NOT_DETERMINED"
    assert result["official_statutory_generation"] == "BLOCKED"


def test_deterministic_snapshot():
    first = assemble_hr_review(inventory())
    second = assemble_hr_review(inventory())
    assert first["snapshot_sha256"] == second["snapshot_sha256"]


def test_added_source_produces_review_correction():
    before = assemble_hr_review(inventory())
    updated = inventory()
    updated["source_inventory"][0]["dated_evidence"][0][
        "source_sha256"
    ] = HASH_B

    after = assemble_hr_review(updated)
    result = compare_hr_reviews(before, after)

    assert result["change_count"] == 1
    assert result["changes"][0]["change_type"] == "CHANGED_SOURCE"
    assert result["statutory_correction_generated"] is False


def test_unchanged_sources_produce_no_corrections():
    first = assemble_hr_review(inventory())
    result = compare_hr_reviews(first, deepcopy(first))
    assert result["change_count"] == 0


def test_tampered_snapshot_rejected():
    result = assemble_hr_review(inventory())
    result["snapshot"]["records"][0]["source_sha256"] = HASH_B

    with pytest.raises(D5D6WorkflowError):
        verify_review_snapshot(result)


@pytest.mark.parametrize(
    "mutation",
    [
        "wrong_company",
        "bad_digest",
        "duplicate",
        "wrong_counter",
        "unapproved_mapping",
        "incorrect_missing_tables",
        "unblocked_filing",
    ],
)
def test_invalid_workflow_rejected(mutation):
    data = inventory()
    section = data["source_inventory"][0]
    row = section["dated_evidence"][0]

    if mutation == "wrong_company":
        row["company_id"] = 99

    elif mutation == "bad_digest":
        row["source_sha256"] = "INVALID"

    elif mutation == "duplicate":
        section["dated_evidence"].append(deepcopy(row))
        section["dated_in_period_count"] = 2
        section["scanned_count"] = 2
        data["dated_evidence_count"] = 2

    elif mutation == "wrong_counter":
        data["dated_evidence_count"] = 99

    elif mutation == "unapproved_mapping":
        row["statutory_classification"] = "UNVERIFIED_CODE"

    elif mutation == "incorrect_missing_tables":
        data["missing_source_tables"] = []

    elif mutation == "unblocked_filing":
        data["official_statutory_generation"] = "READY"

    with pytest.raises(D5D6WorkflowError):
        assemble_hr_review(data)


def test_cross_company_correction_rejected():
    first = assemble_hr_review(inventory())
    other = inventory()
    other["company_id"] = 2
    other["source_inventory"][0]["dated_evidence"][0][
        "company_id"
    ] = 2

    second = assemble_hr_review(other)

    with pytest.raises(D5D6WorkflowError):
        compare_hr_reviews(first, second)
