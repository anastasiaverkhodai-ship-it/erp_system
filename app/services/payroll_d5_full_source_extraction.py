"""Read-only, source-bound extraction of local D5 evidence.

Extraction never establishes statutory authenticity or approves
official D5 field mappings.
"""

from io import BytesIO
from hashlib import sha256
from html.parser import HTMLParser
from pathlib import Path
from zipfile import ZipFile, BadZipFile
from xml.etree import ElementTree as ET
import json
import re
import shutil
import subprocess


MAX_FILE = 25 * 1024 * 1024
MAX_ZIP_MEMBER = 30 * 1024 * 1024
MAX_ZIP_TOTAL = 100 * 1024 * 1024

NS_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS_M = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
NS_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS_P = "http://schemas.openxmlformats.org/package/2006/relationships"


class SourceExtractionError(ValueError):
    pass


class HTMLText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.hidden += 1
        if not self.hidden and tag in (
            "p", "div", "tr", "li", "br", "h1", "h2", "h3"
        ):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self.hidden:
            self.hidden -= 1
        if not self.hidden and tag in ("p", "div", "tr", "li"):
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def normalise(value):
    return re.sub(r"\s+", " ", value).strip()


def _safe_zip(data):
    try:
        archive = ZipFile(BytesIO(data))
        members = archive.infolist()

        if sum(i.file_size for i in members) > MAX_ZIP_TOTAL:
            raise SourceExtractionError(
                "ZIP expanded size exceeds safety limit"
            )

        if any(i.file_size > MAX_ZIP_MEMBER for i in members):
            raise SourceExtractionError(
                "ZIP member exceeds safety limit"
            )

        if len(members) > 20000:
            raise SourceExtractionError(
                "Excessive ZIP member count"
            )

        return archive

    except BadZipFile as exc:
        raise SourceExtractionError(
            "Invalid office archive"
        ) from exc


def _zip_xml(archive, name):
    return ET.fromstring(archive.read(name))


def _docx(data):
    archive = _safe_zip(data)

    with archive:
        root = _zip_xml(
            archive, "word/document.xml"
        )

    body = root.find(f"{{{NS_W}}}body")

    if body is None:
        return []

    blocks = []

    for node in body:
        if node.tag == f"{{{NS_W}}}p":
            text = normalise("".join(
                x.text or ""
                for x in node.iter(f"{{{NS_W}}}t")
            ))
            if text:
                blocks.append({
                    "kind": "paragraph",
                    "locator": f"paragraph:{len(blocks) + 1}",
                    "text": text,
                })

        elif node.tag == f"{{{NS_W}}}tbl":
            rows = []

            for tr in node.findall(f"{{{NS_W}}}tr"):
                cells = []

                for tc in tr.findall(f"{{{NS_W}}}tc"):
                    cells.append(normalise(" ".join(
                        x.text or ""
                        for x in tc.iter(f"{{{NS_W}}}t")
                    )))

                rows.append(cells)

            blocks.append({
                "kind": "table",
                "locator": f"table:{len(blocks) + 1}",
                "rows": rows,
            })

    return blocks


def _xlsx(data):
    archive = _safe_zip(data)

    with archive:
        book = _zip_xml(
            archive, "xl/workbook.xml"
        )

        relationships = _zip_xml(
            archive, "xl/_rels/workbook.xml.rels"
        )

        relmap = {
            r.attrib.get("Id"): r.attrib.get("Target", "")
            for r in relationships
        }

        shared = []

        if "xl/sharedStrings.xml" in archive.namelist():
            ss = _zip_xml(
                archive, "xl/sharedStrings.xml"
            )

            for item in ss.findall(f"{{{NS_M}}}si"):
                shared.append("".join(
                    x.text or ""
                    for x in item.iter(f"{{{NS_M}}}t")
                ))

        blocks = []

        for sheet in book.findall(
            f".//{{{NS_M}}}sheet"
        ):
            sheet_name = sheet.attrib.get("name", "")
            relationship_id = sheet.attrib.get(
                f"{{{NS_R}}}id"
            )

            target = relmap.get(relationship_id, "")

            if target.startswith("/xl/"):
                filename = target.lstrip("/")
            elif target.startswith("/"):
                raise SourceExtractionError(
                    "Unexpected XLSX relationship target"
                )
            else:
                filename = str(
                    Path("xl") / target
                )

            from posixpath import normpath
            filename = normpath(filename)

            if (
                not filename.startswith("xl/")
                or filename not in archive.namelist()
            ):
                raise SourceExtractionError(
                    "XLSX worksheet target missing"
                )

            root = _zip_xml(archive, filename)

            for row in root.findall(
                f".//{{{NS_M}}}sheetData/{{{NS_M}}}row"
            ):
                cells = []

                for cell in row.findall(f"{{{NS_M}}}c"):
                    ref = cell.attrib.get("r", "")
                    kind = cell.attrib.get("t")
                    v = cell.find(f"{{{NS_M}}}v")

                    value = v.text if v is not None else ""

                    if kind == "s" and value:
                        idx = int(value)

                        if idx < 0 or idx >= len(shared):
                            raise SourceExtractionError(
                                "Invalid shared string index"
                            )

                        value = shared[idx]

                    if kind == "inlineStr":
                        value = "".join(
                            n.text or ""
                            for n in cell.iter(f"{{{NS_M}}}t")
                        )

                    formula = cell.find(f"{{{NS_M}}}f")

                    cells.append({
                        "cell": ref,
                        "value": value,
                        "formula": (
                            formula.text if formula is not None
                            else None
                        ),
                    })

                if cells:
                    blocks.append({
                        "kind": "worksheet_row",
                        "locator": (
                            f"{sheet_name}!row:"
                            f"{row.attrib.get('r', '')}"
                        ),
                        "cells": cells,
                    })

        return blocks


def _pdf(data):
    try:
        import fitz
    except ImportError:
        fitz = None

    if fitz is not None:
        with fitz.open(
            stream=data, filetype="pdf"
        ) as pdf:
            return [
                {
                    "kind": "pdf_page",
                    "locator": f"page:{i + 1}",
                    "text": page.get_text("text"),
                }
                for i, page in enumerate(pdf)
            ]

    try:
        from pypdf import PdfReader
    except ImportError:
        PdfReader = None

    if PdfReader is not None:
        pdf = PdfReader(BytesIO(data))

        return [
            {
                "kind": "pdf_page",
                "locator": f"page:{i + 1}",
                "text": page.extract_text() or "",
            }
            for i, page in enumerate(pdf.pages)
        ]

    if shutil.which("pdftotext"):
        result = subprocess.run(
            ["pdftotext", "-layout", "-", "-"],
            input=data,
            capture_output=True,
            timeout=60,
        )

        if result.returncode:
            raise SourceExtractionError(
                "pdftotext failed"
            )

        pages = result.stdout.decode(
            "utf-8", errors="replace"
        ).split("\f")

        if pages and not pages[-1].strip():
            pages.pop()

        return [
            {
                "kind": "pdf_page",
                "locator": f"page:{i + 1}",
                "text": text,
            }
            for i, text in enumerate(pages)
        ]

    raise SourceExtractionError(
        "PDF parser unavailable"
    )


def extract_document(path, relative=None):
    path = Path(path)

    if path.is_symlink() or not path.is_file():
        raise SourceExtractionError(
            "Regular non-symlinked source required"
        )

    data = path.read_bytes()

    if not data or len(data) > MAX_FILE:
        raise SourceExtractionError(
            "Empty or oversized source"
        )

    name = relative or path.name
    suffix = path.suffix.lower()
    digest = sha256(data).hexdigest()

    if suffix == ".docx":
        blocks = _docx(data)

    elif suffix == ".xlsx":
        blocks = _xlsx(data)

    elif suffix == ".pdf":
        blocks = _pdf(data)

    elif suffix == ".doc":
        if not shutil.which("textutil"):
            raise SourceExtractionError(
                "Legacy DOC converter unavailable"
            )

        result = subprocess.run(
            [
                "textutil", "-convert", "txt",
                "-stdout", str(path),
            ],
            capture_output=True,
            timeout=60,
        )

        if result.returncode:
            raise SourceExtractionError(
                "Legacy DOC conversion failed"
            )

        blocks = [{
            "kind": "legacy_document_text",
            "locator": "document:1",
            "text": result.stdout.decode(
                "utf-8", errors="replace"
            ),
        }]

    elif suffix in (".html", ".htm"):
        parser = HTMLText()
        parser.feed(data.decode("utf-8-sig"))

        blocks = [{
            "kind": "html_text",
            "locator": "document:1",
            "text": "".join(parser.parts),
        }]

    elif suffix == ".json":
        value = json.loads(
            data.decode("utf-8-sig")
        )

        blocks = [{
            "kind": "json_structure",
            "locator": "document:1",
            "text": json.dumps(
                value, ensure_ascii=False,
                sort_keys=True,
            ),
        }]

    elif suffix == ".txt":
        blocks = [{
            "kind": "plain_text",
            "locator": "document:1",
            "text": data.decode("utf-8-sig"),
        }]

    else:
        raise SourceExtractionError(
            "Unsupported source format: " + suffix
        )

    output = []

    for block in blocks:
        value = json.dumps(
            block,
            ensure_ascii=False,
            sort_keys=True,
        )

        output.append({
            **block,
            "block_sha256": sha256(
                value.encode("utf-8")
            ).hexdigest(),
        })

    textual = sum(
        bool(
            str(item.get("text", "")).strip()
            or item.get("rows")
            or item.get("cells")
        )
        for item in output
    )

    return {
        "source": name,
        "source_sha256": digest,
        "size_bytes": len(data),
        "blocks": output,
        "block_count": len(output),
        "nonempty_blocks": textual,
        "status": (
            "EXTRACTED_REVIEW_ONLY"
            if textual
            else "NO_EXTRACTABLE_CONTENT"
        ),
        "legal_authenticity": "NOT_APPROVED",
        "statutory_mapping": "NOT_APPROVED",
        "official_generation": "BLOCKED",
    }


def extract_directory(root):
    root = Path(root).resolve(strict=True)

    if not root.is_dir():
        raise SourceExtractionError(
            "Source directory required"
        )

    sources = []

    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise SourceExtractionError(
                "Symlink in normative sources"
            )

        if not path.is_file():
            continue

        relative = path.relative_to(root).as_posix()

        try:
            result = extract_document(path, relative)
        except Exception as exc:
            result = {
                "source": relative,
                "status": "EXTRACTION_BLOCKED",
                "error_type": type(exc).__name__,
                "error": str(exc)[:300],
                "blocks": [],
                "legal_authenticity": "NOT_APPROVED",
                "statutory_mapping": "NOT_APPROVED",
                "official_generation": "BLOCKED",
            }

        sources.append(result)

    return {
        "source_count": len(sources),
        "extracted_count": sum(
            source["status"] == "EXTRACTED_REVIEW_ONLY"
            for source in sources
        ),
        "blocked_count": sum(
            source["status"] != "EXTRACTED_REVIEW_ONLY"
            for source in sources
        ),
        "sources": sources,
        "status": "REVIEW_REQUIRED",
        "official_generation": "BLOCKED",
    }
