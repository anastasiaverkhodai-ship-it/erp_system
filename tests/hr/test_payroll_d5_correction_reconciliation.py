from copy import deepcopy

import pytest

from app.services.payroll_d5_correction_reconciliation import (
    D5CorrectionReconciliationError,
    reconcile_d5_correction_candidate,
)


def chain(state="NO_REVERSAL_RECORDED"):
    reversed_event = state == "SOURCE_REVERSED"

    return {
        "company_id": 1,
        "employment_event_id": 10,
        "chain_event_ids": (
            [10, 11] if reversed_event else [10]
        ),
        "chain_state": state,
        "blockers": (
            ["AMBIGUOUS_CORRECTION_BRANCH"]
            if state == "REVIEW_BLOCKED" else []
        ),
        "reversal_count": 1 if reversed_event else 0,
        "correction_chain_sha256": "a" * 64,
        "official_generation": "BLOCKED",
        "official_rows_generated": 0,
    }


def projection():
    return {
        "source_binding": (
            "PERSISTED_POSTGRESQL_RECORDS_VERIFIED"
        ),
        "candidate": {
            "identity": {
                "company_id": 1,
                "employment_event_id": 10,
            }
        },
        "persisted_erp_evidence_sha256": "b" * 64,
        "official_generation": "BLOCKED",
        "official_rows_generated": 0,
    }


def review(c, p=None):
    return reconcile_d5_correction_candidate(
        company_id=1,
        employment_event_id=10,
        chain=c,
        persisted_projection=p,
    )


def test_active_candidate_retained():
    result = review(chain(), projection())

    assert result["decision"] == "CANDIDATE_RETAINED"
    assert result["candidate_rows_retained"] == 1
    assert result["candidate_rows_suppressed"] == 0
    assert result["official_generation"] == "BLOCKED"


def test_reversed_candidate_suppressed():
    result = review(chain("SOURCE_REVERSED"))

    assert result["decision"] == "CANDIDATE_SUPPRESSED"
    assert result["candidate_rows_retained"] == 0
    assert result["candidate_rows_suppressed"] == 1
    assert result["official_rows_generated"] == 0


def test_ambiguous_chain_blocks():
    result = review(chain("REVIEW_BLOCKED"))

    assert result["decision"] == "REVIEW_BLOCKED"
    assert result["candidate_rows_retained"] == 0


def test_reproducible_sha256():
    assert review(chain(), projection())[
        "reconciliation_sha256"
    ] == review(chain(), projection())[
        "reconciliation_sha256"
    ]


def test_missing_active_projection_rejected():
    with pytest.raises(
        D5CorrectionReconciliationError,
        match="projection required",
    ):
        review(chain())


def test_suppressed_projection_rejected():
    with pytest.raises(
        D5CorrectionReconciliationError,
        match="cannot retain projection",
    ):
        review(chain("SOURCE_REVERSED"), projection())


def test_cross_company_rejected():
    item = chain()
    item["company_id"] = 999

    with pytest.raises(
        D5CorrectionReconciliationError,
        match="identity mismatch",
    ):
        review(item, projection())


def test_projection_identity_mismatch_rejected():
    item = projection()
    item["candidate"]["identity"]["employment_event_id"] = 999

    with pytest.raises(
        D5CorrectionReconciliationError,
        match="candidate identity mismatch",
    ):
        review(chain(), item)


def test_duplicate_chain_event_rejected():
    item = chain("SOURCE_REVERSED")
    item["chain_event_ids"] = [10, 10]

    with pytest.raises(
        D5CorrectionReconciliationError,
        match="Malformed",
    ):
        review(item)


def test_forged_official_state_rejected():
    item = chain()
    item["official_generation"] = "APPROVED"

    with pytest.raises(
        D5CorrectionReconciliationError,
        match="Unsafe correction chain",
    ):
        review(item, projection())


def test_forged_official_rows_rejected():
    item = chain()
    item["official_rows_generated"] = 1

    with pytest.raises(
        D5CorrectionReconciliationError,
        match="Official correction",
    ):
        review(item, projection())


def test_bad_hash_rejected():
    item = chain()
    item["correction_chain_sha256"] = "not-a-hash"

    with pytest.raises(
        D5CorrectionReconciliationError,
        match="digest",
    ):
        review(item, projection())


def test_unknown_chain_state_blocks():
    item = chain()
    item["chain_state"] = "UNSUPPORTED"

    result = review(item)
    assert result["decision"] == "REVIEW_BLOCKED"


@pytest.mark.parametrize("bad", [None, True, 0, -1, "1"])
def test_invalid_company_rejected(bad):
    with pytest.raises(D5CorrectionReconciliationError):
        reconcile_d5_correction_candidate(
            company_id=bad,
            employment_event_id=10,
            chain=chain(),
        )
