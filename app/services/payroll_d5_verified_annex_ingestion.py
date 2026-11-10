"""Fail-closed candidate-only D5 amendment annex ingestion.

Local annex content and SHA-256 matching do NOT establish
independent legal authenticity or statutory approval.

No database writes and no statutory report generation.
"""

import csv
from datetime import date
from hashlib import sha256
import io
import json
from pathlib import Path
import re

from app.services.payroll_d5_amendment_reconciliation import (
    D5AmendmentEvidence,
)
from app.services.payroll_d5_profession_effective_history import (
    D5ProfessionHistoryChange,
)


class D5AnnexIngestionError(ValueError):
    pass


EXPECTED_DATES = {
    1: date(2012, 9, 1),
    6: date(2017, 11, 1),
    7: date(2017, 11, 30),
}

SHA = re.compile(r"[0-9a-f]{64}\Z")
MAX_BYTES = 5 * 1024 * 1024
KINDS = {"introduced", "removed", "restored"}
CSV_FIELDS = ["profession_code", "change_kind"]


def _source_file(root, relative_path):
    if type(relative_path) is not str:
        raise D5AnnexIngestionError("Invalid source path")

    relative = Path(relative_path)

    if (
        relative.is_absolute()
        or not relative.parts
        or ".." in relative.parts
    ):
        raise D5AnnexIngestionError(
            "Source must remain within source root"
        )

    root = Path(root).resolve(strict=True)
    current = root

    for part in relative.parts:
        current = current / part

        if current.is_symlink():
            raise D5AnnexIngestionError(
                "Symlinked evidence is forbidden"
            )

    resolved = current.resolve(strict=True)

    if not resolved.is_relative_to(root):
        raise D5AnnexIngestionError(
            "Source escaped root"
        )

    if not resolved.is_file():
        raise D5AnnexIngestionError(
            "Regular evidence file required"
        )

    if resolved.stat().st_size > MAX_BYTES:
        raise D5AnnexIngestionError(
            "Annex exceeds size limit"
        )

    return resolved


def review_d5_annex_manifest(
    *,
    source_root,
    manifest_path,
):
    """Load explicit annex metadata and CSV as review-only evidence.

    Expected manifest fields:
      amendment_number, effective_date, source_reference,
      source_sha256, annex_path, annex_sha256,
      document_completeness, profession_list_completeness.

    Each CSV has profession_code,change_kind columns.
    """
    path = _source_file(source_root, manifest_path)

    try:
        manifest = json.loads(path.read_text("utf-8"))
    except (ValueError, UnicodeError) as exc:
        raise D5AnnexIngestionError(
            "Invalid annex manifest JSON"
        ) from exc

    if type(manifest) is not dict:
        raise D5AnnexIngestionError(
            "Manifest object required"
        )

    required = {
        "amendment_number",
        "effective_date",
        "source_reference",
        "source_sha256",
        "annex_path",
        "annex_sha256",
        "document_completeness",
        "profession_list_completeness",
    }

    if set(manifest) != required:
        raise D5AnnexIngestionError(
            "Incomplete or unexpected manifest fields"
        )

    number = manifest["amendment_number"]

    if type(number) is not int or number < 1:
        raise D5AnnexIngestionError(
            "Invalid amendment number"
        )

    try:
        effective = date.fromisoformat(
            manifest["effective_date"]
        )
    except (TypeError, ValueError) as exc:
        raise D5AnnexIngestionError(
            "Invalid effective date"
        ) from exc

    if (
        number in EXPECTED_DATES
        and effective != EXPECTED_DATES[number]
    ):
        raise D5AnnexIngestionError(
            "Confirmed amendment date mismatch"
        )

    for key in ("source_sha256", "annex_sha256"):
        value = manifest[key]

        if (
            type(value) is not str
            or SHA.fullmatch(value) is None
            or value == "0" * 64
        ):
            raise D5AnnexIngestionError(
                f"Invalid {key}"
            )

    reference = manifest["source_reference"]

    if (
        type(reference) is not str
        or not reference.strip()
        or reference != reference.strip()
    ):
        raise D5AnnexIngestionError(
            "Invalid source reference"
        )

    for field in (
        "document_completeness",
        "profession_list_completeness",
    ):
        if manifest[field] not in (
            "FULL_DOCUMENT_VERIFIED",
            "PARTIAL",
            "NOT_VERIFIED",
        ):
            raise D5AnnexIngestionError(
                "Invalid completeness status"
            )

    annex_file = _source_file(
        source_root,
        manifest["annex_path"],
    )

    data = annex_file.read_bytes()

    if sha256(data).hexdigest() != manifest["annex_sha256"]:
        raise D5AnnexIngestionError(
            "Annex SHA256 mismatch"
        )

    try:
        content = data.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(content))

        if reader.fieldnames != CSV_FIELDS:
            raise D5AnnexIngestionError(
                "Exact annex CSV columns required"
            )

        rows = list(reader)

    except UnicodeError as exc:
        raise D5AnnexIngestionError(
            "Invalid annex UTF-8"
        ) from exc

    if not rows or len(rows) > 100000:
        raise D5AnnexIngestionError(
            "Invalid annex row count"
        )

    seen = set()
    changes = []

    for row in rows:
        if set(row) != set(CSV_FIELDS):
            raise D5AnnexIngestionError(
                "Malformed annex CSV row"
            )

        code = row["profession_code"]
        kind = row["change_kind"]

        if (
            type(code) is not str
            or not code.strip()
            or code != code.strip()
            or kind not in KINDS
        ):
            raise D5AnnexIngestionError(
                "Invalid profession change"
            )

        if code in seen:
            raise D5AnnexIngestionError(
                "Duplicate profession code in annex"
            )

        seen.add(code)

        changes.append(D5ProfessionHistoryChange(
            profession_code=code,
            change_kind=kind,
            effective_date=effective,
            source_reference=reference,
            source_sha256=manifest["source_sha256"],
        ))

    if number == 7 and (
        len(changes) != 10
        or any(
            item.change_kind != "restored"
            for item in changes
        )
    ):
        raise D5AnnexIngestionError(
            "Amendment 7 requires ten unique restorations"
        )

    evidence = D5AmendmentEvidence(
        amendment_number=number,
        effective_date=effective,
        source_reference=reference,
        source_sha256=manifest["source_sha256"],
        document_completeness=(
            manifest["document_completeness"]
        ),
        profession_list_completeness=(
            manifest["profession_list_completeness"]
        ),
    )

    return {
        "amendment": evidence,
        "changes": tuple(changes),
        "annex_sha256": sha256(data).hexdigest(),
        "candidate_rows": len(changes),
        "source_binding": "LOCAL_BYTES_HASH_MATCH",
        "independent_authenticity": "NOT_VERIFIED",
        "full_classifier_history": "NOT_VERIFIED",
        "normative_approval": "NOT_APPROVED",
        "official_generation": "BLOCKED",
        "official_rows_generated": 0,
    }
