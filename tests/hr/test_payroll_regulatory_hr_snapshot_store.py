import pytest

from app.services.payroll_regulatory_hr_snapshot_store import (
    HRReviewSnapshotError,
    canonical,
    digest,
    validate_stored_snapshot,
)


def test_canonical_payload_is_stable():
    a = {"b": 2, "a": 1}
    b = {"a": 1, "b": 2}

    assert canonical(a) == canonical(b)
    assert digest(a) == digest(b)


def test_different_data_changes_digest():
    assert digest({"x": 1}) != digest({"x": 2})


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        {"schema": "UNSUPPORTED"},
        {
            "schema": "HR_REVIEW_SNAPSHOT_V1",
            "report_kind": "d1",
            "review": {},
            "official_statutory_generation": "BLOCKED",
        },
    ],
)
def test_invalid_snapshot_rejected(payload):
    with pytest.raises(Exception):
        validate_stored_snapshot(payload)


def _valid_test_payload():
    from app.services.payroll_regulatory_d5_d6_workflow import (
        assemble_hr_review,
    )

    inventory = {
        "company_id": 1,
        "payroll_period_id": 2,
        "period_start": "2026-10-01",
        "period_end": "2026-10-31",
        "review_only": True,
        "official_statutory_generation": "BLOCKED",
        "dated_evidence_count": 0,
        "ambiguous_evidence_count": 0,
        "source_inventory": [],
        "missing_source_tables": [
            "hr_changes",
            "employment_contracts",
            "payroll_d5_professional_evidence",
        ],
    }

    return {
        "schema": "HR_REVIEW_SNAPSHOT_V1",
        "identity": {
            "company_id": 1,
            "payroll_period_id": 2,
            "payroll_regulatory_report_id": 3,
            "report_kind": "d5",
            "report_revision": 1,
            "previous_snapshot_id": None,
        },
        "review": assemble_hr_review(inventory),
        "official_statutory_generation": "BLOCKED",
    }


def test_nested_d5_identity_accepted():
    assert validate_stored_snapshot(_valid_test_payload())


def test_nested_d6_identity_accepted():
    payload = _valid_test_payload()
    payload["identity"]["report_kind"] = "d6"
    assert validate_stored_snapshot(payload)


@pytest.mark.parametrize(
    "mutation",
    [
        "kind",
        "company",
        "period",
        "revision",
        "report_id",
        "previous_id",
        "generation",
        "missing_identity",
    ],
)
def test_snapshot_identity_corruption_rejected(mutation):
    payload = _valid_test_payload()
    identity = payload["identity"]

    if mutation == "kind":
        identity["report_kind"] = "d1"
    elif mutation == "company":
        identity["company_id"] = 999
    elif mutation == "period":
        identity["payroll_period_id"] = 999
    elif mutation == "revision":
        identity["report_revision"] = 0
    elif mutation == "report_id":
        identity["payroll_regulatory_report_id"] = -1
    elif mutation == "previous_id":
        identity["previous_snapshot_id"] = -2
    elif mutation == "generation":
        payload["official_statutory_generation"] = "READY"
    elif mutation == "missing_identity":
        del payload["identity"]

    with pytest.raises(HRReviewSnapshotError):
        validate_stored_snapshot(payload)
