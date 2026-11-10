import csv
from datetime import date
from hashlib import sha256
import json

import pytest

from app.services.payroll_d5_verified_annex_ingestion import (
    D5AnnexIngestionError,
    review_d5_annex_manifest,
)


def fixture(tmp_path, *, number=7, count=10):
    annex = tmp_path / "annex.csv"

    with annex.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["profession_code", "change_kind"])
        for index in range(count):
            writer.writerow([
                f"TEST-{index:02d}",
                "restored" if number == 7 else "introduced",
            ])

    data = annex.read_bytes()

    manifest = {
        "amendment_number": number,
        "effective_date": (
            "2017-11-30" if number == 7
            else "2017-11-01"
        ),
        "source_reference": "SYNTHETIC-TEST-SOURCE",
        "source_sha256": "a" * 64,
        "annex_path": "annex.csv",
        "annex_sha256": sha256(data).hexdigest(),
        "document_completeness": "PARTIAL",
        "profession_list_completeness": "PARTIAL",
    }

    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    return path, annex, manifest


def review(tmp_path):
    return review_d5_annex_manifest(
        source_root=tmp_path,
        manifest_path="manifest.json",
    )


def test_ten_candidate_restorations(tmp_path):
    fixture(tmp_path)
    result = review(tmp_path)

    assert result["candidate_rows"] == 10
    assert len(result["changes"]) == 10
    assert result["source_binding"] == "LOCAL_BYTES_HASH_MATCH"
    assert result["normative_approval"] == "NOT_APPROVED"
    assert result["official_generation"] == "BLOCKED"
    assert result["official_rows_generated"] == 0


def test_nine_restorations_rejected(tmp_path):
    fixture(tmp_path, count=9)

    with pytest.raises(
        D5AnnexIngestionError,
        match="ten unique",
    ):
        review(tmp_path)


def test_tampered_annex_rejected(tmp_path):
    _, annex, _ = fixture(tmp_path)

    with annex.open("a", encoding="utf-8") as handle:
        handle.write("EXTRA,restored\n")

    with pytest.raises(
        D5AnnexIngestionError,
        match="SHA256 mismatch",
    ):
        review(tmp_path)


def test_incorrect_effective_date_rejected(tmp_path):
    path, _, manifest = fixture(tmp_path)

    manifest["effective_date"] = "2017-10-26"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(
        D5AnnexIngestionError,
        match="date mismatch",
    ):
        review(tmp_path)


def test_path_traversal_rejected(tmp_path):
    path, _, manifest = fixture(tmp_path)

    manifest["annex_path"] = "../outside.csv"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(D5AnnexIngestionError):
        review(tmp_path)


def test_symlink_rejected(tmp_path):
    path, annex, manifest = fixture(tmp_path)

    link = tmp_path / "linked.csv"
    link.symlink_to(annex)

    manifest["annex_path"] = "linked.csv"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(
        D5AnnexIngestionError,
        match="Symlink",
    ):
        review(tmp_path)


def test_duplicate_profession_rejected(tmp_path):
    path, annex, manifest = fixture(tmp_path)

    rows = annex.read_text().splitlines()
    rows[-1] = rows[1]
    annex.write_text("\n".join(rows) + "\n")

    manifest["annex_sha256"] = sha256(
        annex.read_bytes()
    ).hexdigest()

    path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(
        D5AnnexIngestionError,
        match="Duplicate profession",
    ):
        review(tmp_path)


def test_deterministic_candidate_values(tmp_path):
    fixture(tmp_path)

    first = review(tmp_path)
    second = review(tmp_path)

    assert first["annex_sha256"] == second["annex_sha256"]
    assert first["changes"] == second["changes"]
    assert first["full_classifier_history"] == "NOT_VERIFIED"


def test_amendment_six_date(tmp_path):
    fixture(tmp_path, number=6, count=3)

    result = review(tmp_path)

    assert result["amendment"].effective_date == date(
        2017, 11, 1
    )
