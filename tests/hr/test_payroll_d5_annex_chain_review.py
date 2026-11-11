"""Synthetic candidate evidence, never official D5 approval."""

from hashlib import sha256
import json

import pytest

from app.services.payroll_d5_annex_chain_review import (
    D5AnnexChainError,
    review_d5_annex_chain,
)


def make_annex(
    root,
    number,
    effective_date,
    *,
    code="1234",
    kind="introduced",
    completeness="PARTIAL",
):
    csv_bytes = (
        "profession_code,change_kind\n"
        f"{code},{kind}\n"
    ).encode()

    annex_name = f"annex_{number}.csv"
    manifest_name = f"manifest_{number}.json"

    (root / annex_name).write_bytes(csv_bytes)

    manifest = {
        "amendment_number": number,
        "effective_date": effective_date,
        "source_reference": f"TEST-SOURCE-{number}",
        "source_sha256": sha256(
            f"synthetic-document-{number}".encode()
        ).hexdigest(),
        "annex_path": annex_name,
        "annex_sha256": sha256(csv_bytes).hexdigest(),
        "document_completeness": completeness,
        "profession_list_completeness": completeness,
    }

    (root / manifest_name).write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )

    return manifest_name


def review(root, paths):
    return review_d5_annex_chain(
        source_root=root,
        manifest_paths=paths,
    )


def test_chain_is_order_independent_and_blocked(tmp_path):
    a = make_annex(
        tmp_path, 2, "2013-01-01",
        code="1234", kind="introduced",
    )
    b = make_annex(
        tmp_path, 3, "2014-01-01",
        code="1234", kind="removed",
    )

    first = review(tmp_path, [a, b])
    second = review(tmp_path, [b, a])

    assert first["chain_sha256"] == second["chain_sha256"]
    assert first["amendment_count"] == 2
    assert first["candidate_event_count"] == 2
    assert first["missing_amendment_numbers"] == (1,)
    assert first["review_only"] is True
    assert first["official_generation"] == "BLOCKED"
    assert first["official_rows_generated"] == 0
    assert first["normative_approval"] == "NOT_APPROVED"


def test_duplicate_manifest_rejected(tmp_path):
    a = make_annex(tmp_path, 2, "2013-01-01")

    with pytest.raises(
        D5AnnexChainError,
        match="Duplicate annex manifest",
    ):
        review(tmp_path, [a, a])


def test_duplicate_amendment_number_rejected(tmp_path):
    a = make_annex(tmp_path, 2, "2013-01-01")

    b = make_annex(
        tmp_path, 3, "2014-01-01",
    )

    data = json.loads((tmp_path / b).read_text())
    data["amendment_number"] = 2
    (tmp_path / b).write_text(json.dumps(data))

    with pytest.raises(
        D5AnnexChainError,
        match="Duplicate amendment number",
    ):
        review(tmp_path, [a, b])


def test_reverse_chronology_rejected(tmp_path):
    a = make_annex(tmp_path, 2, "2015-01-01")
    b = make_annex(tmp_path, 3, "2014-01-01")

    with pytest.raises(
        D5AnnexChainError,
        match="chronology",
    ):
        review(tmp_path, [a, b])


def test_same_day_profession_conflict_rejected(tmp_path):
    a = make_annex(
        tmp_path, 2, "2014-01-01", code="1234",
    )
    b = make_annex(
        tmp_path, 3, "2014-01-01", code="1234",
    )

    with pytest.raises(D5AnnexChainError):
        review(tmp_path, [a, b])


def test_tampered_annex_rejected(tmp_path):
    a = make_annex(tmp_path, 2, "2013-01-01")

    (tmp_path / "annex_2.csv").write_text(
        "profession_code,change_kind\n9999,removed\n"
    )

    with pytest.raises(
        D5AnnexChainError,
        match="source validation",
    ):
        review(tmp_path, [a])


def test_path_traversal_rejected(tmp_path):
    with pytest.raises(
        D5AnnexChainError,
        match="source validation",
    ):
        review(tmp_path, ["../outside.json"])


def test_missing_history_never_becomes_approved(tmp_path):
    a = make_annex(
        tmp_path,
        2,
        "2013-01-01",
        completeness="FULL_DOCUMENT_VERIFIED",
    )

    result = review(tmp_path, [a])

    assert result["missing_amendment_numbers"] == (1,)
    assert result["independent_authenticity"] == "NOT_VERIFIED"
    assert result["full_classifier_history"] == "NOT_VERIFIED"
    assert result["official_generation"] == "BLOCKED"


def test_invalid_manifest_collection_rejected(tmp_path):
    for value in (None, (), [], "manifest.json"):
        with pytest.raises(D5AnnexChainError):
            review(tmp_path, value)
