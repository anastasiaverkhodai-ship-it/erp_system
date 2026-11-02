"""Offline import of Ukrainian payroll normative source artifacts.

Accepted inputs: actual PDF or XLSX files supplied locally.
No network requests; no synthetic files; no automatic approvals.
The import manifest proves local byte integrity only.
"""

import argparse
import hashlib
import io
import json
import os
import shutil
import zipfile
from pathlib import Path
from xml.etree import ElementTree

MAX_SIZE = 25 * 1024 * 1024

FORM_FILES = {
    "ORDER_243": "order_243.pdf",
    "ORDER_284": "order_284.pdf",
    "D1": "legal_entity_d1.xlsx",
    "4DF": "legal_entity_4df.xlsx",
    "D5": "legal_entity_d5.xlsx",
    "D6": "legal_entity_d6.xlsx",
}


def check_structure(data, filename):
    if not isinstance(data, bytes) or not 0 < len(data) <= MAX_SIZE:
        raise ValueError("Missing or oversized source artifact")

    if filename.endswith(".pdf"):
        if not data.startswith(b"%PDF-"):
            raise ValueError("Invalid PDF signature")
        return

    if not filename.endswith(".xlsx"):
        raise ValueError("Unsupported artifact format")

    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        members = set(archive.namelist())

        if not {
            "[Content_Types].xml",
            "xl/workbook.xml",
        } <= members:
            raise ValueError("Missing XLSX workbook structure")

        for entry in archive.infolist():
            if (
                entry.file_size > MAX_SIZE
                or entry.filename.startswith("/")
                or ".." in Path(entry.filename).parts
            ):
                raise ValueError("Unsafe XLSX member")

        ElementTree.fromstring(
            archive.read("xl/workbook.xml")
        )


def safe_directory(path):
    path = Path(path).expanduser()

    if path.is_symlink():
        raise ValueError("Symlink directory forbidden")

    if not path.is_dir():
        raise ValueError("Directory does not exist")

    return path.resolve(strict=True)


def import_sources(input_dir, output_dir):
    source = safe_directory(input_dir)

    target = Path(output_dir).expanduser()

    if target.is_symlink():
        raise ValueError("Symlink destination forbidden")

    target.mkdir(parents=True, exist_ok=True)
    target = safe_directory(target)

    found = []

    for source_id, filename in FORM_FILES.items():
        path = source / filename

        if path.is_symlink():
            raise ValueError("Symlink source file forbidden")

        if not path.exists():
            continue

        if not path.is_file():
            raise ValueError("Source must be a regular file")

        if path.stat().st_size > MAX_SIZE:
            raise ValueError("Source too large")

        data = path.read_bytes()
        check_structure(data, filename)

        digest = hashlib.sha256(data).hexdigest()
        destination = target / filename

        if destination.is_symlink():
            raise ValueError("Symlink destination file forbidden")

        if destination.exists():
            existing = destination.read_bytes()

            if hashlib.sha256(existing).hexdigest() != digest:
                raise ValueError(
                    "Existing artifact differs: " + filename
                )
        else:
            with destination.open("xb") as handle:
                handle.write(data)

        assert hashlib.sha256(
            destination.read_bytes()
        ).hexdigest() == digest

        found.append({
            "source_id": source_id,
            "filename": filename,
            "sha256": digest,
            "bytes": len(data),
            "structure": "CHECKED",
            "publisher_authenticity": "PENDING_REVIEW",
            "legal_revision": "PENDING_REVIEW",
            "effective_dates": "PENDING_REVIEW",
            "mapping_approved": False,
        })

    manifest = {
        "schema": "ERP_PAYROLL_OFFLINE_SOURCES_V1",
        "artifacts": found,
        "count": len(found),
        "official_generation": "BLOCKED",
        "normative_approval": False,
    }

    manifest_file = target / "offline_manifest.json"

    if manifest_file.is_symlink():
        raise ValueError("Symlink manifest forbidden")

    if manifest_file.exists():
        previous = json.loads(manifest_file.read_text())

        old = {
            item["source_id"]: item["sha256"]
            for item in previous["artifacts"]
        }
        new = {
            item["source_id"]: item["sha256"]
            for item in found
        }

        if old != new:
            raise ValueError(
                "Manifest changed: manual review required"
            )
    else:
        with manifest_file.open("x") as handle:
            json.dump(
                manifest, handle,
                indent=2,
                ensure_ascii=False,
            )

    return manifest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    result = import_sources(
        args.input_dir,
        args.output_dir,
    )

    print("IMPORTED SOURCE ARTIFACTS =", result["count"])
    print("SOURCE INTEGRITY = CHECKED")
    print("LEGAL AUTHENTICITY = REVIEW REQUIRED")
    print("OFFICIAL GENERATION = BLOCKED")


if __name__ == "__main__":
    main()
