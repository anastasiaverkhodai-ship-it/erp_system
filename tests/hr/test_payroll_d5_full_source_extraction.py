from hashlib import sha256
from pathlib import Path
from zipfile import ZipFile

import pytest

from app.services.payroll_d5_full_source_extraction import (
    SourceExtractionError,
    extract_directory,
    extract_document,
)


def test_html_source_hash_and_text(tmp_path):
    path = tmp_path / "source.html"
    path.write_text(
        "<script>ignore</script>"
        "<p>Додаток Д5. Графа 12.</p>",
        encoding="utf-8",
    )

    result = extract_document(path)

    assert result["source_sha256"] == sha256(
        path.read_bytes()
    ).hexdigest()
    assert "Графа 12" in result["blocks"][0]["text"]
    assert "ignore" not in result["blocks"][0]["text"]
    assert result["official_generation"] == "BLOCKED"


def test_docx_real_table_extraction(tmp_path):
    path = tmp_path / "source.docx"

    xml = (
        '<w:document xmlns:w="'
        'http://schemas.openxmlformats.org/'
        'wordprocessingml/2006/main">'
        '<w:body><w:tbl><w:tr><w:tc>'
        '<w:p><w:r><w:t>Графа 12</w:t></w:r></w:p>'
        '</w:tc></w:tr></w:tbl></w:body></w:document>'
    )

    with ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", xml)

    result = extract_document(path)

    assert result["blocks"][0]["kind"] == "table"
    assert result["blocks"][0]["rows"][0][0] == "Графа 12"


def test_xlsx_actual_cell_coordinates(tmp_path):
    path = tmp_path / "classifier.xlsx"

    workbook = (
        '<workbook xmlns="'
        'http://schemas.openxmlformats.org/'
        'spreadsheetml/2006/main"'
        ' xmlns:r="http://schemas.openxmlformats.org/'
        'officeDocument/2006/relationships">'
        '<sheets><sheet name="Codes" sheetId="1"'
        ' r:id="rId1"/></sheets></workbook>'
    )

    rels = (
        '<Relationships xmlns="'
        'http://schemas.openxmlformats.org/'
        'package/2006/relationships">'
        '<Relationship Id="rId1"'
        ' Target="worksheets/sheet1.xml"/></Relationships>'
    )

    sheet = (
        '<worksheet xmlns="'
        'http://schemas.openxmlformats.org/'
        'spreadsheetml/2006/main">'
        '<sheetData><row r="2">'
        '<c r="A2" t="inlineStr"><is><t>'
        'Професія</t></is></c>'
        '</row></sheetData></worksheet>'
    )

    with ZipFile(path, "w") as archive:
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr(
            "xl/_rels/workbook.xml.rels", rels
        )
        archive.writestr(
            "xl/worksheets/sheet1.xml", sheet
        )

    result = extract_document(path)
    row = result["blocks"][0]

    assert row["locator"] == "Codes!row:2"
    assert row["cells"][0]["cell"] == "A2"
    assert row["cells"][0]["value"] == "Професія"
    assert result["statutory_mapping"] == "NOT_APPROVED"


def test_invalid_docx_is_blocked(tmp_path):
    path = tmp_path / "bad.docx"
    path.write_bytes(b"not an office archive")

    with pytest.raises(SourceExtractionError):
        extract_document(path)


def test_directory_reports_unreadable_source(tmp_path):
    (tmp_path / "valid.txt").write_text(
        "Verified local text", encoding="utf-8"
    )
    (tmp_path / "bad.docx").write_bytes(b"bad")

    report = extract_directory(tmp_path)

    assert report["source_count"] == 2
    assert report["extracted_count"] == 1
    assert report["blocked_count"] == 1
    assert report["official_generation"] == "BLOCKED"


def test_symlink_rejected(tmp_path):
    source = tmp_path / "source.txt"
    source.write_text("test")

    (tmp_path / "linked.txt").symlink_to(source)

    with pytest.raises(
        SourceExtractionError,
        match="Symlink",
    ):
        extract_directory(tmp_path)
