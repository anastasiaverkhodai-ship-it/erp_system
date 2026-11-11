import hashlib
import json

import pytest

from app.services.payroll_d5_annex_event_reconciliation import (
    reconcile_d5_annex_events,
)


def make(root, number, when, code, kind):
    csv_content = (
        f"profession_code,change_kind\n{code},{kind}\n"
    ).encode()

    annex = f"annex_{number}.csv"
    name = f"manifest_{number}.json"

    (root / annex).write_bytes(csv_content)
    payload = {
        "amendment_number": number,
        "effective_date": when,
        "source_reference": f"TEST-{number}",
        "source_sha256": hashlib.sha256(
            f"test-source-{number}".encode()
        ).hexdigest(),
        "annex_path": annex,
        "annex_sha256": hashlib.sha256(
            csv_content
        ).hexdigest(),
        "document_completeness": "PARTIAL",
        "profession_list_completeness": "PARTIAL",
    }
    (root / name).write_text(json.dumps(payload))
    return name


def review(root, items):
    return reconcile_d5_annex_events(
        source_root=root,
        manifest_paths=items,
    )


def test_introduce_then_remove_consistent(tmp_path):
    a = make(tmp_path, 2, "2013-01-01", "1234", "introduced")
    b = make(tmp_path, 3, "2014-01-01", "1234", "removed")
    result = review(tmp_path, [b, a])
    assert result["issues"] == []
    assert result["profession_reviews"][0][
        "last_observed_state"
    ] == "inactive"
    assert result["official_generation"] == "BLOCKED"


def test_double_introduction_flagged(tmp_path):
    a = make(tmp_path, 2, "2013-01-01", "1234", "introduced")
    b = make(tmp_path, 3, "2014-01-01", "1234", "introduced")
    result = review(tmp_path, [a, b])
    assert result["issues"][0]["issue"] == (
        "ACTIVE_PROFESSION_REACTIVATED"
    )


def test_double_removal_flagged(tmp_path):
    a = make(tmp_path, 2, "2013-01-01", "1234", "removed")
    b = make(tmp_path, 3, "2014-01-01", "1234", "removed")
    result = review(tmp_path, [a, b])
    assert result["issues"][0]["issue"] == (
        "INACTIVE_PROFESSION_REMOVED_AGAIN"
    )


def test_gap_prevents_false_conflict(tmp_path):
    a = make(tmp_path, 2, "2013-01-01", "1234", "introduced")
    b = make(tmp_path, 4, "2015-01-01", "1234", "introduced")
    result = review(tmp_path, [a, b])
    assert result["issues"] == []
    assert result["profession_reviews"][0]["requires_review"]


def test_missing_prior_state_never_approved(tmp_path):
    a = make(tmp_path, 2, "2013-01-01", "1234", "restored")
    result = review(tmp_path, [a])
    assert result["profession_reviews"][0]["requires_review"]
    assert result["normative_approval"] == "NOT_APPROVED"
    assert result["official_rows_generated"] == 0


def test_order_independent_digest(tmp_path):
    a = make(tmp_path, 2, "2013-01-01", "1234", "introduced")
    b = make(tmp_path, 3, "2014-01-01", "1234", "removed")
    left = review(tmp_path, [a, b])
    right = review(tmp_path, [b, a])
    assert left["reconciliation_sha256"] == right[
        "reconciliation_sha256"
    ]


def test_tampered_annex_rejected(tmp_path):
    a = make(tmp_path, 2, "2013-01-01", "1234", "introduced")
    (tmp_path / "annex_2.csv").write_text(
        "profession_code,change_kind\n9999,removed\n"
    )
    with pytest.raises(ValueError):
        review(tmp_path, [a])


def test_different_professions_independent(tmp_path):
    a = make(tmp_path, 2, "2013-01-01", "1234", "introduced")
    b = make(tmp_path, 3, "2014-01-01", "5678", "introduced")
    result = review(tmp_path, [a, b])
    assert result["issues"] == []
    assert result["profession_count"] == 2
