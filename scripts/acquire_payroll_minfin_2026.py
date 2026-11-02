"""Acquire actual 2026 legal-entity payroll forms from Ukraine's MinFin.

The files are source artifacts, not approvals or certified mappings.
Do not generate official reports from this evidence alone.
"""

import argparse
import hashlib
import json
import subprocess
import zipfile
from pathlib import Path
from urllib.parse import unquote
from xml.etree import ElementTree

MAX_BYTES = 25 * 1024 * 1024
ROOT = "https://www.mof.gov.ua/storage/files/"

SOURCES = {
    "MINFIN_243_ORDER": (
        "https://mof.gov.ua/storage/files/"
        "%D0%9D%D0%B0%D0%BA%D0%B0%D0%B7%20243.pdf",
        "order_243.pdf",
    ),
    "LEGAL_ENTITY_D1": (
        ROOT + "1_1_2_%20%D0%AE%D0%9E_%D0%941%20"
        "%D0%9C%D0%A4%D0%A3%281%29.xlsx",
        "legal_entity_d1.xlsx",
    ),
    "LEGAL_ENTITY_4DF": (
        ROOT + "1_1_5_%20%20%D0%AE%D0%9E_4%D0%94%D0%A4_"
        "%D0%9C%D0%A4%D0%A3%281%29.xlsx",
        "legal_entity_4df.xlsx",
    ),
    "LEGAL_ENTITY_D5": (
        ROOT + "1_1_6_%20%D0%AE%D0%9E_%D0%945_"
        "%D0%9C%D0%A4%D0%A3%281%29.xlsx",
        "legal_entity_d5.xlsx",
    ),
    "LEGAL_ENTITY_D6": (
        ROOT + "1_1_7_%20%D0%AE%D0%9E_%D0%946_"
        "%D0%9C%D0%A4%D0%A3%281%29.xlsx",
        "legal_entity_d6.xlsx",
    ),
}


def validate(data, filename):
    if not data or len(data) > MAX_BYTES:
        raise ValueError("Empty or oversized source")

    if filename.endswith(".pdf"):
        if not data.startswith(b"%PDF-"):
            raise ValueError("Not a PDF document")
        return

    if not zipfile.is_zipfile(__import__("io").BytesIO(data)):
        raise ValueError("Not an XLSX ZIP container")

    import io
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        members = set(archive.namelist())
        if not {
            "[Content_Types].xml",
            "xl/workbook.xml",
        } <= members:
            raise ValueError("Invalid XLSX structure")

        if any(
            info.file_size > MAX_BYTES
            for info in archive.infolist()
        ):
            raise ValueError("Oversized XLSX member")

        ElementTree.fromstring(
            archive.read("xl/workbook.xml")
        )


def fetch(url):
    last_error = None
    for attempt in range(1, 3):
        result = subprocess.run(
            [
                "curl", "--fail", "--location",
                "--silent", "--show-error",
                "--proto", "=https",
                "--proto-redir", "=https",
                "--connect-timeout", "12",
                "--max-time", "45",
                "--max-filesize", str(MAX_BYTES),
                "--url", url,
            ],
            capture_output=True,
            timeout=55,
        )
        if result.returncode == 0 and result.stdout:
            return result.stdout

        last_error = (
            f"attempt={attempt} curl={result.returncode} "
            + result.stderr.decode(
                "utf-8", errors="replace"
            )[:160]
        )
        print("DOWNLOAD RETRY:", last_error, flush=True)

    raise RuntimeError(
        "Official source unavailable: " + str(last_error)
    )


def acquire(root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)

    if root.is_symlink():
        raise ValueError("Symlink source directory forbidden")

    artifacts = []

    for source_id, (url, filename) in SOURCES.items():
        target = root / filename

        if target.is_symlink():
            raise ValueError("Symlink source file forbidden")

        if target.exists():
            data = target.read_bytes()
            origin = "PRESERVED_EXISTING"
        else:
            data = fetch(url)
            origin = "HTTPS_MINFIN_DOWNLOAD"

        validate(data, filename)
        digest = hashlib.sha256(data).hexdigest()

        if not target.exists():
            with target.open("xb") as output:
                output.write(data)

        if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
            raise ValueError("Stored artifact digest mismatch")

        artifacts.append({
            "source_id": source_id,
            "url": url,
            "filename": filename,
            "sha256": digest,
            "size": len(data),
            "origin": origin,
            "structure": "VALID",
            "legal_revision_review": "PENDING",
            "effective_date_review": "PENDING",
            "publisher_content_review": "PENDING",
            "mapping_approved": False,
        })

        print(
            f"ACTUAL SOURCE {source_id} = PASS "
            f"({len(data)} bytes, sha256={digest})",
            flush=True,
        )

    manifest = {
        "schema": "MINFIN_2026_FORM_ARTIFACTS_V1",
        "artifacts": artifacts,
        "source_count": len(artifacts),
        "official_generation": "BLOCKED",
        "normative_approval": False,
    }

    manifest_path = root / "manifest.json"

    if manifest_path.is_symlink():
        raise ValueError("Symlink manifest forbidden")

    if manifest_path.exists():
        old = json.loads(manifest_path.read_text())
        old_hashes = {
            r["source_id"]: r["sha256"]
            for r in old["artifacts"]
        }
        new_hashes = {
            r["source_id"]: r["sha256"]
            for r in artifacts
        }
        if old_hashes != new_hashes:
            raise ValueError("Prior manifest changed")
    else:
        manifest_path.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False)
        )

    return manifest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", required=True)
    args = parser.parse_args()
    result = acquire(args.source_dir)
    print("ACTUAL MINFIN DOCUMENTS =", result["source_count"])
    print("OFFICIAL GENERATION = BLOCKED")


if __name__ == "__main__":
    main()
