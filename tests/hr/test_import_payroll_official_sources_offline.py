import io
import zipfile

import pytest

from scripts.import_payroll_official_sources_offline import (
    FORM_FILES,
    check_structure,
    import_sources,
)


def make_xlsx():
    buffer = io.BytesIO()

    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "[Content_Types].xml", "<Types/>"
        )
        archive.writestr(
            "xl/workbook.xml", "<workbook/>"
        )

    return buffer.getvalue()


def test_six_expected_source_slots():
    assert set(FORM_FILES) == {
        "ORDER_243", "ORDER_284",
        "D1", "4DF", "D5", "D6",
    }


def test_pdf_signature():
    check_structure(b"%PDF-1.7\nTEST", "order_243.pdf")


def test_html_rejected():
    with pytest.raises(ValueError):
        check_structure(
            b"<html>Access denied</html>",
            "order_243.pdf",
        )


def test_valid_xlsx():
    check_structure(
        make_xlsx(),
        "legal_entity_d1.xlsx",
    )


def test_invalid_xlsx():
    with pytest.raises(zipfile.BadZipFile):
        check_structure(b"not a zip", "legal_entity_d1.xlsx")


def test_real_offline_import_and_idempotency(tmp_path):
    src = tmp_path / "source"
    dst = tmp_path / "destination"

    src.mkdir()
    (src / "order_243.pdf").write_bytes(
        b"%PDF-1.7\nSYNTHETIC TEST ONLY"
    )
    (src / "legal_entity_d1.xlsx").write_bytes(
        make_xlsx()
    )

    first = import_sources(src, dst)
    second = import_sources(src, dst)

    assert first["count"] == 2
    assert second["count"] == 2
    assert first["official_generation"] == "BLOCKED"
    assert not first["normative_approval"]
    assert all(
        not item["mapping_approved"]
        for item in first["artifacts"]
    )


def test_tampered_source_rejected(tmp_path):
    src = tmp_path / "src"
    dst = tmp_path / "dst"

    src.mkdir()
    (src / "order_243.pdf").write_bytes(
        b"%PDF-1.7\nORIGINAL"
    )

    import_sources(src, dst)

    (src / "order_243.pdf").write_bytes(
        b"%PDF-1.7\nCHANGED"
    )

    with pytest.raises(ValueError, match="differs"):
        import_sources(src, dst)


def test_symlink_file_rejected(tmp_path):
    src = tmp_path / "src"
    dst = tmp_path / "dst"

    src.mkdir()

    original = tmp_path / "original.pdf"
    original.write_bytes(b"%PDF-1.7\nTEST")

    (src / "order_243.pdf").symlink_to(original)

    with pytest.raises(ValueError, match="Symlink"):
        import_sources(src, dst)
