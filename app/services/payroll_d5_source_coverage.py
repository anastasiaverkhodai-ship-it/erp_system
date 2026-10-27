
"""Fail-closed coverage validation for historical classifier sources.

File acquisition does not establish legal effect or source approval.
"""
from dataclasses import dataclass
from datetime import date
from typing import Sequence


class D5SourceCoverageError(ValueError):
    pass


@dataclass(frozen=True)
class D5SourceCoverageRecord:
    source_id: str
    acquired: bool
    content_reviewed: bool
    effective_from: date | None
    independently_approved: bool
    sha256: str | None


def validate_classifier_source_coverage(
    records: Sequence[D5SourceCoverageRecord],
    *,
    amendment_count: int = 17,
) -> None:
    if type(amendment_count) is not int or amendment_count < 1:
        raise D5SourceCoverageError("Invalid amendment count")

    if not isinstance(records, (tuple, list)):
        raise D5SourceCoverageError("Coverage records required")

    expected = {"base"} | {
        f"amendment_{n}" for n in range(1, amendment_count + 1)
    }

    actual = []
    for record in records:
        if not isinstance(record, D5SourceCoverageRecord):
            raise D5SourceCoverageError("Invalid source record")
        actual.append(record.source_id)

    if set(actual) != expected or len(actual) != len(expected):
        raise D5SourceCoverageError(
            "Classifier source inventory incomplete or duplicated"
        )

    for record in records:
        if not (
            record.acquired
            and record.content_reviewed
            and record.independently_approved
            and type(record.effective_from) is date
            and isinstance(record.sha256, str)
            and len(record.sha256) == 64
            and all(c in "0123456789abcdef" for c in record.sha256)
        ):
            raise D5SourceCoverageError(
                f"Unverified classifier source: {record.source_id}"
            )
