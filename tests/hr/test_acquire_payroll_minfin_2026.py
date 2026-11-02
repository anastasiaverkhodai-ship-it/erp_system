import io
import zipfile

import pytest

from scripts.acquire_payroll_minfin_2026 import (
    SOURCES,
    validate,
)


def test_five_official_source_targets():
    assert len(SOURCES) == 5
    assert len({name for _, name in SOURCES.values()}) == 5
    assert all(
        url.startswith("https://")
        for url, _ in SOURCES.values()
    )


def test_pdf_magic():
    validate(b"%PDF-1.7\nTEST", "order_243.pdf")


def test_html_masquerading_as_pdf_rejected():
    with pytest.raises(ValueError):
        validate(b"<html>blocked</html>", "order_243.pdf")


def test_invalid_xlsx_rejected():
    with pytest.raises(ValueError):
        validate(b"<html>not xlsx</html>", "form.xlsx")


def test_valid_minimal_xlsx():
    output = io.BytesIO()

    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(
            "[Content_Types].xml", "<Types/>"
        )
        archive.writestr(
            "xl/workbook.xml", "<workbook/>"
        )

    validate(output.getvalue(), "form.xlsx")
