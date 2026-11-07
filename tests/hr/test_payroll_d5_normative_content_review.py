import hashlib
import json

import pytest

from app.services.payroll_d5_normative_content_review import (
    D5ContentReviewError,
    build_content_inventory,
    discover_review_evidence,
)


def test_actual_ukrainian_excerpt():
    content = (
        "Додаток Д5 подається якщо укладено або "
        "розірвано трудовий договір."
    ).encode()

    result = discover_review_evidence("source.html", content)

    match = next(
        x for x in result["matches"]
        if x["topic"] == "hire_termination"
    )

    assert match["meaning_verified"] is False
    assert match["legal_authority_verified"] is False
    assert match["field_mapping_approved"] is False
    assert match["status"] == "REVIEW_REQUIRED"
    assert len(match["excerpt_sha256"]) == 64


def test_unrelated_text_produces_no_rule():
    result = discover_review_evidence(
        "source.txt",
        b"Completely unrelated text",
    )
    assert result["matches"] == []


def test_unreadable_binary_format_not_falsely_extracted():
    result = discover_review_evidence(
        "source.pdf",
        b"%PDF-1.7 binary file",
    )
    assert result["status"] == (
        "FORMAT_REQUIRES_SEPARATE_REVIEW"
    )
    assert result["matches"] == []


def test_javascript_not_mistaken_for_visible_html():
    content = (
        "<script>укладено розірвано трудовий</script>"
        "<p>Other text</p>"
    ).encode()
    result = discover_review_evidence(
        "source.html", content
    )
    assert result["matches"] == []


def test_invalid_encoding_rejected():
    with pytest.raises(D5ContentReviewError):
        discover_review_evidence(
            "source.txt", b"\xff\xfe\xff"
        )


def test_invalid_json_rejected():
    with pytest.raises(json.JSONDecodeError):
        discover_review_evidence(
            "source.json", b"{bad json"
        )


def test_inventory_real_files_and_missing_coverage(tmp_path):
    (tmp_path / "source.html").write_text(
        "<p>укладено або розірвано трудовий договір</p>",
        encoding="utf-8",
    )
    (tmp_path / "scan.pdf").write_bytes(
        b"%PDF-1.7 test"
    )

    result = build_content_inventory(tmp_path)

    assert result["source_file_count"] == 2
    assert "hire_termination" in result["candidate_topics"]
    assert "termination_legal_basis" in result["missing_topics"]
    assert result["official_generation"] == "BLOCKED"
    assert result["official_field_mapping_approval"] is False


def test_changed_source_changes_digest():
    first = discover_review_evidence(
        "source.txt", b"Version 1"
    )
    second = discover_review_evidence(
        "source.txt", b"Version 2"
    )
    assert first["sha256"] != second["sha256"]


def test_symlinked_source_rejected(tmp_path):
    (tmp_path / "real.txt").write_text("test")
    (tmp_path / "link.txt").symlink_to(
        tmp_path / "real.txt"
    )

    with pytest.raises(
        D5ContentReviewError,
        match="Symlinked",
    ):
        build_content_inventory(tmp_path)


def test_all_found_topics_remain_unapproved(tmp_path):
    (tmp_path / "source.txt").write_text(
        " ".join([
            "укладено розірвано трудов",
            "переведен посад",
            "причини звільнення закону",
            "основного місця роботи сумісництво",
            "нового робочого місця дата",
            "уточнюючий додаток д5",
        ]),
        encoding="utf-8",
    )

    result = build_content_inventory(tmp_path)

    assert result["candidate_topic_count"] == 6
    assert result["missing_topics"] == []
    assert result["legal_source_approval"] is False
    assert result["official_field_mapping_approval"] is False
    assert result["official_rows_generated"] == 0
