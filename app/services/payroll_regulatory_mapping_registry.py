"""Independently approved, dated statutory mapping registry.

Entries must be introduced through a separately reviewed code change
with genuine official sources. A report row cannot approve itself.
"""
from dataclasses import dataclass
from datetime import date


class RegulatoryMappingError(ValueError):
    pass


@dataclass(frozen=True)
class ApprovedRegulatoryMapping:
    mapping_id: int
    report_kind: str
    effective_from: date
    effective_to: date | None
    official_reference: str
    source_sha256: str
    approved: bool


# This list is empty until normative sources are independently
# verified and explicitly approved. Never populate from user
# input, an unreviewed import, or report row payloads.
APPROVED_MAPPINGS: tuple[ApprovedRegulatoryMapping, ...] = ()


def approved_mappings_for(
    report_kind: str,
    period_start: date,
    period_end: date,
):
    if report_kind not in {"d1", "4df", "d5", "d6"}:
        raise RegulatoryMappingError("Unsupported report kind")

    if period_end < period_start:
        raise RegulatoryMappingError("Invalid reporting interval")

    result = {}
    for item in APPROVED_MAPPINGS:
        if not item.approved or item.report_kind != report_kind:
            continue

        if not item.official_reference.strip():
            raise RegulatoryMappingError(
                "Approved mapping lacks official reference"
            )

        digest = item.source_sha256
        if (
            len(digest) != 64
            or any(c not in "0123456789abcdef" for c in digest)
        ):
            raise RegulatoryMappingError(
                "Approved mapping lacks pinned source digest"
            )

        if (
            item.effective_from > period_start
            or (
                item.effective_to is not None
                and item.effective_to < period_end
            )
        ):
            continue

        if item.mapping_id in result:
            raise RegulatoryMappingError(
                "Duplicate independently approved mapping"
            )

        result[item.mapping_id] = item

    return result
