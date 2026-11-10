"""Review-only D5 profession effective-history reconstruction.

Declared amendments are candidate evidence, not proof of
complete or legally approved profession-classifier history.
"""

from dataclasses import dataclass
from datetime import date
from hashlib import sha256
import json
import re


class D5ProfessionHistoryError(ValueError):
    pass


VALID_CHANGES = frozenset({
    "introduced", "removed", "restored",
})

SHA = re.compile(r"[0-9a-f]{64}\Z")


@dataclass(frozen=True)
class D5ProfessionHistoryChange:
    profession_code: str
    change_kind: str
    effective_date: date
    source_reference: str
    source_sha256: str


def _required(value, field):
    if (
        type(value) is not str
        or not value.strip()
        or value != value.strip()
    ):
        raise D5ProfessionHistoryError(
            f"Invalid {field}"
        )


def review_d5_profession_history(
    *,
    profession_code,
    reporting_date,
    changes,
):
    _required(profession_code, "profession_code")

    if type(reporting_date) is not date:
        raise D5ProfessionHistoryError(
            "Exact reporting date required"
        )

    if type(changes) not in (list, tuple):
        raise D5ProfessionHistoryError(
            "History collection required"
        )

    if len(changes) > 10000:
        raise D5ProfessionHistoryError(
            "History collection limit exceeded"
        )

    records = []

    for change in changes:
        if type(change) is not D5ProfessionHistoryChange:
            raise D5ProfessionHistoryError(
                "Typed history change required"
            )

        if change.profession_code != profession_code:
            raise D5ProfessionHistoryError(
                "Cross-profession history contamination"
            )

        if change.change_kind not in VALID_CHANGES:
            raise D5ProfessionHistoryError(
                "Unsupported history change"
            )

        if type(change.effective_date) is not date:
            raise D5ProfessionHistoryError(
                "Invalid change effective date"
            )

        _required(change.source_reference, "source_reference")

        if (
            type(change.source_sha256) is not str
            or SHA.fullmatch(change.source_sha256) is None
            or change.source_sha256 == "0" * 64
        ):
            raise D5ProfessionHistoryError(
                "Invalid source SHA256"
            )

        records.append(change)

    records.sort(key=lambda x: x.effective_date)

    for previous, current in zip(records, records[1:]):
        if previous.effective_date == current.effective_date:
            raise D5ProfessionHistoryError(
                "Conflicting same-date amendments"
            )

        if (
            previous.change_kind in ("introduced", "restored")
            and current.change_kind in ("introduced", "restored")
        ):
            raise D5ProfessionHistoryError(
                "Repeated activation without removal"
            )

        if (
            previous.change_kind == "removed"
            and current.change_kind == "removed"
        ):
            raise D5ProfessionHistoryError(
                "Repeated removal without restoration"
            )

    current = None

    for record in records:
        if record.effective_date <= reporting_date:
            current = record
        else:
            break

    if current is None:
        status = "NOT_VERIFIED"
    elif current.change_kind == "removed":
        status = "CANDIDATE_INACTIVE"
    else:
        status = "CANDIDATE_ACTIVE"

    payload = [
        {
            "profession_code": item.profession_code,
            "kind": item.change_kind,
            "date": item.effective_date.isoformat(),
            "reference": item.source_reference,
            "sha256": item.source_sha256,
        }
        for item in records
    ]

    digest = sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()

    return {
        "profession_code": profession_code,
        "reporting_date": reporting_date.isoformat(),
        "candidate_state": status,
        "history_sha256": digest,
        "changes_considered": len(records),
        "effective_change_date": (
            current.effective_date.isoformat()
            if current else None
        ),
        "full_amendment_coverage": "NOT_VERIFIED",
        "source_authenticity": "NOT_VERIFIED",
        "normative_approval": "NOT_APPROVED",
        "official_generation": "BLOCKED",
        "official_rows_generated": 0,
    }
