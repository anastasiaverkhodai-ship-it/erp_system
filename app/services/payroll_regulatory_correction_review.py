"""Internal regulatory correction and source reconciliation review.

Compares two validated, source-derived review states.

No official correction rows are generated. This module never approves
a D5 event code, D6 applicability, or statutory field mapping.
"""

import base64
import hashlib
import json
import math
from datetime import date, datetime, time
from decimal import Decimal
from enum import Enum
from uuid import UUID

from app.services.payroll_regulatory_unified_review import (
    build_unified_regulatory_review,
    validate_unified_review,
    validate_hr_snapshot_identity,
)


class RegulatoryCorrectionReviewError(ValueError):
    pass


REPORT_KINDS = ("d1", "4df", "d5", "d6")
MAX_DEPTH = 32
MAX_COLLECTION_ITEMS = 100000


def normalize(value, depth=0):
    """Canonical, non-lossy conversion of supported source values."""

    if depth > MAX_DEPTH:
        raise RegulatoryCorrectionReviewError(
            "Regulatory source nesting limit exceeded"
        )

    if value is None or type(value) in (bool, int, str):
        return value

    if isinstance(value, Decimal):
        if not value.is_finite():
            raise RegulatoryCorrectionReviewError(
                "Non-finite regulatory amount"
            )
        return {"__decimal__": str(value)}

    if type(value) is float:
        if not math.isfinite(value):
            raise RegulatoryCorrectionReviewError(
                "Non-finite regulatory number"
            )
        return {"__float__": repr(value)}

    if isinstance(value, datetime):
        if value.tzinfo is None:
            return {"__datetime_naive__": value.isoformat()}
        return {"__datetime__": value.isoformat()}

    if isinstance(value, date):
        return {"__date__": value.isoformat()}

    if isinstance(value, time):
        return {"__time__": value.isoformat()}

    if isinstance(value, UUID):
        return {"__uuid__": str(value)}

    if isinstance(value, Enum):
        return {
            "__enum__": (
                value.__class__.__module__
                + "."
                + value.__class__.__qualname__
            ),
            "value": normalize(value.value, depth + 1),
        }

    if isinstance(value, bytes):
        return {
            "__bytes_base64__": base64.b64encode(value).decode("ascii")
        }

    if isinstance(value, (list, tuple)):
        if len(value) > MAX_COLLECTION_ITEMS:
            raise RegulatoryCorrectionReviewError(
                "Regulatory source collection too large"
            )
        return [normalize(item, depth + 1) for item in value]

    if isinstance(value, dict):
        if len(value) > MAX_COLLECTION_ITEMS:
            raise RegulatoryCorrectionReviewError(
                "Regulatory source mapping too large"
            )

        if any(type(key) is not str for key in value):
            raise RegulatoryCorrectionReviewError(
                "Regulatory source keys must be strings"
            )

        return {
            key: normalize(item, depth + 1)
            for key, item in sorted(value.items())
        }

    raise RegulatoryCorrectionReviewError(
        "Unsupported regulatory source type: "
        + type(value).__name__
    )


def canonical(value):
    return json.dumps(
        normalize(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def digest(value):
    return hashlib.sha256(
        canonical(value).encode("utf-8")
    ).hexdigest()


def validate_regulatory_review(review):
    """Validate scope, fail-closed status and HR snapshot identities."""

    validate_unified_review(review)

    for kind in REPORT_KINDS:
        section = review["reports"][kind]

        if kind in ("d1", "4df"):
            if not isinstance(section.get("source_readiness"), dict):
                raise RegulatoryCorrectionReviewError(
                    "Missing financial source readiness: " + kind
                )

        else:
            if section.get("statutory_mapping") != "NOT_APPROVED":
                raise RegulatoryCorrectionReviewError(
                    "Unapproved HR mapping state: " + kind
                )

            if kind == "d6" and (
                section.get("applicability") != "NOT_DETERMINED"
            ):
                raise RegulatoryCorrectionReviewError(
                    "Unverified D6 applicability state"
                )

            source_hash = section.get(
                "hr_source_snapshot_sha256"
            )

            if not isinstance(source_hash, str) or len(source_hash) != 64:
                raise RegulatoryCorrectionReviewError(
                    "Invalid HR evidence digest: " + kind
                )

            try:
                int(source_hash, 16)
            except ValueError as exc:
                raise RegulatoryCorrectionReviewError(
                    "Malformed HR evidence digest"
                ) from exc

    canonical(review)
    return True


def snapshot_regulatory_review(review):
    """Produce deterministic source digests without exposing raw PII."""

    validate_regulatory_review(review)

    section_digests = {
        kind: digest(review["reports"][kind])
        for kind in REPORT_KINDS
    }

    return {
        "schema": "REGULATORY_INTERNAL_SOURCE_REVIEW_V1",
        "company_id": review["company_id"],
        "payroll_period_id": review["payroll_period_id"],
        "section_digests": section_digests,
        "overall_sha256": digest({
            "company_id": review["company_id"],
            "payroll_period_id": review["payroll_period_id"],
            "sections": section_digests,
        }),
        "review_only": True,
        "official_generation": "BLOCKED",
    }


def validate_review_snapshot(snapshot):
    if not isinstance(snapshot, dict):
        raise RegulatoryCorrectionReviewError(
            "Review snapshot required"
        )

    if snapshot.get("schema") != (
        "REGULATORY_INTERNAL_SOURCE_REVIEW_V1"
    ):
        raise RegulatoryCorrectionReviewError(
            "Unexpected regulatory snapshot schema"
        )

    for field in ("company_id", "payroll_period_id"):
        value = snapshot.get(field)
        if type(value) is not int or value <= 0:
            raise RegulatoryCorrectionReviewError(
                "Invalid snapshot scope: " + field
            )

    sections = snapshot.get("section_digests")

    if not isinstance(sections, dict) or set(sections) != set(REPORT_KINDS):
        raise RegulatoryCorrectionReviewError(
            "Incomplete regulatory snapshot coverage"
        )

    for value in (*sections.values(), snapshot.get("overall_sha256")):
        if (
            not isinstance(value, str)
            or len(value) != 64
        ):
            raise RegulatoryCorrectionReviewError(
                "Invalid regulatory snapshot hash"
            )

        try:
            int(value, 16)
        except ValueError as exc:
            raise RegulatoryCorrectionReviewError(
                "Malformed regulatory snapshot hash"
            ) from exc

    if snapshot.get("review_only") is not True:
        raise RegulatoryCorrectionReviewError(
            "Snapshot must remain review-only"
        )

    if snapshot.get("official_generation") != "BLOCKED":
        raise RegulatoryCorrectionReviewError(
            "Official generation must remain blocked"
        )

    expected = digest({
        "company_id": snapshot["company_id"],
        "payroll_period_id": snapshot["payroll_period_id"],
        "sections": sections,
    })

    if expected != snapshot["overall_sha256"]:
        raise RegulatoryCorrectionReviewError(
            "Regulatory snapshot integrity mismatch"
        )

    return True


def compare_regulatory_snapshots(before, after):
    """Find source revisions, not statutory correction declarations."""

    validate_review_snapshot(before)
    validate_review_snapshot(after)

    if (
        before["company_id"] != after["company_id"]
        or before["payroll_period_id"] != after["payroll_period_id"]
    ):
        raise RegulatoryCorrectionReviewError(
            "Cannot compare different companies or payroll periods"
        )

    differences = []

    for kind in REPORT_KINDS:
        previous = before["section_digests"][kind]
        current = after["section_digests"][kind]

        if previous != current:
            differences.append({
                "report_kind": kind,
                "previous_source_sha256": previous,
                "current_source_sha256": current,
                "status": "SOURCE_REVIEW_REQUIRED",
            })

    return {
        "company_id": before["company_id"],
        "payroll_period_id": before["payroll_period_id"],
        "before_sha256": before["overall_sha256"],
        "after_sha256": after["overall_sha256"],
        "changed_report_count": len(differences),
        "changed_reports": differences,
        "correction_status": (
            "SOURCE_REVIEW_REQUIRED"
            if differences
            else "NO_SOURCE_CHANGES"
        ),
        "financial_reconciliation": "NOT_RECOMPUTED",
        "statutory_correction_generated": False,
        "official_generation": "BLOCKED",
    }


async def build_regulatory_correction_review(
    db,
    *,
    company_id,
    payroll_period_id,
    before_review,
    d5_snapshot_id=None,
    d6_snapshot_id=None,
):
    """Requery current source review and compare against a prior state."""

    before = snapshot_regulatory_review(before_review)

    if (
        before["company_id"] != company_id
        or before["payroll_period_id"] != payroll_period_id
    ):
        raise RegulatoryCorrectionReviewError(
            "Previous review does not match requested scope"
        )

    current = await build_unified_regulatory_review(
        db,
        company_id=company_id,
        payroll_period_id=payroll_period_id,
        d5_snapshot_id=d5_snapshot_id,
        d6_snapshot_id=d6_snapshot_id,
    )

    after = snapshot_regulatory_review(current)
    comparison = compare_regulatory_snapshots(before, after)

    return {
        "current_review": current,
        "current_snapshot": after,
        "comparison": comparison,
        "official_generation": "BLOCKED",
    }
