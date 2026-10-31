"""Append-only PostgreSQL D5/D6 review snapshot persistence.

This stores internal evidence, never filing-ready statutory rows.
"""

import hashlib
import json

from sqlalchemy import text, select

from app.models.payroll_regulatory_report import (
    PayrollRegulatoryReport,
)

from app.services.payroll_regulatory_d5_d6_workflow import (
    build_verified_d5_d6_workflow,
    verify_review_snapshot,
    compare_hr_reviews,
)


class HRReviewSnapshotError(ValueError):
    pass


def canonical(value):
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )


def digest(value):
    return hashlib.sha256(
        canonical(value).encode("utf-8")
    ).hexdigest()


def validate_stored_snapshot(stored):
    """Validate canonical payload and its nested report identity."""
    if not isinstance(stored, dict):
        raise HRReviewSnapshotError("Snapshot object required")

    if stored.get("schema") != "HR_REVIEW_SNAPSHOT_V1":
        raise HRReviewSnapshotError("Unknown snapshot schema")

    if stored.get("official_statutory_generation") != "BLOCKED":
        raise HRReviewSnapshotError("Illegal official generation status")

    identity = stored.get("identity")
    if not isinstance(identity, dict):
        raise HRReviewSnapshotError("Snapshot identity required")

    for field in (
        "company_id",
        "payroll_period_id",
        "payroll_regulatory_report_id",
        "report_revision",
    ):
        value = identity.get(field)
        if type(value) is not int or value <= 0:
            raise HRReviewSnapshotError(
                "Invalid snapshot identity: " + field
            )

    if identity.get("report_kind") not in ("d5", "d6"):
        raise HRReviewSnapshotError("Wrong report kind")

    previous_id = identity.get("previous_snapshot_id")
    if previous_id is not None and (
        type(previous_id) is not int or previous_id <= 0
    ):
        raise HRReviewSnapshotError(
            "Invalid previous snapshot identifier"
        )

    review = stored.get("review")
    if not isinstance(review, dict):
        raise HRReviewSnapshotError("Review payload missing")

    verify_review_snapshot(review)

    if review.get("company_id") != identity["company_id"]:
        raise HRReviewSnapshotError(
            "Review company differs from report identity"
        )

    if review.get("payroll_period_id") != identity["payroll_period_id"]:
        raise HRReviewSnapshotError(
            "Review period differs from report identity"
        )

    if review.get("official_statutory_generation") != "BLOCKED":
        raise HRReviewSnapshotError(
            "Review improperly permits statutory generation"
        )

    return True


async def load_hr_snapshot(
    db,
    *,
    company_id,
    snapshot_id,
):
    row = (
        await db.execute(
            text("""
                SELECT
                    id,
                    company_id,
                    payroll_period_id,
                    payroll_regulatory_report_id,
                    report_kind,
                    report_revision,
                    previous_snapshot_id,
                    payload_json,
                    snapshot_sha256
                FROM payroll_hr_review_snapshots
                WHERE company_id = :company_id
                  AND id = :snapshot_id
            """),
            {
                "company_id": company_id,
                "snapshot_id": snapshot_id,
            },
        )
    ).mappings().one_or_none()

    if row is None:
        raise HRReviewSnapshotError("Snapshot not found")

    try:
        payload = json.loads(row["payload_json"])
    except (TypeError, ValueError) as exc:
        raise HRReviewSnapshotError(
            "Invalid persisted snapshot JSON"
        ) from exc

    if digest(payload) != row["snapshot_sha256"]:
        raise HRReviewSnapshotError(
            "Persisted HR snapshot digest mismatch"
        )

    validate_stored_snapshot(payload)

    identity = payload.get("identity", {})

    for key in (
        "company_id",
        "payroll_period_id",
        "payroll_regulatory_report_id",
        "report_kind",
        "report_revision",
        "previous_snapshot_id",
    ):
        if identity.get(key) != row[key]:
            raise HRReviewSnapshotError(
                "Persisted HR snapshot identity mismatch: " + key
            )

    review = payload["review"]

    if (
        review["company_id"] != row["company_id"]
        or review["payroll_period_id"] != row["payroll_period_id"]
    ):
        raise HRReviewSnapshotError(
            "Persisted review belongs to another period/company"
        )

    return {
        "id": row["id"],
        "payload": payload,
        "sha256": row["snapshot_sha256"],
        "previous_snapshot_id": row["previous_snapshot_id"],
    }


async def persist_hr_review_snapshot(
    db,
    *,
    company_id,
    payroll_regulatory_report_id,
):
    """Create a draft-report-bound immutable source snapshot.

    Calls requiring a revision change must first create a new report
    revision. Existing snapshots cannot be replaced.
    """

    report = await db.scalar(
        select(PayrollRegulatoryReport)
        .where(
            PayrollRegulatoryReport.company_id == company_id,
            PayrollRegulatoryReport.id == payroll_regulatory_report_id,
        )
        .with_for_update()
    )

    if report is None:
        raise HRReviewSnapshotError("Regulatory report not found")

    if report.report_kind not in ("d5", "d6"):
        raise HRReviewSnapshotError(
            "Only D5/D6 review snapshots are supported"
        )

    if report.status != "draft":
        raise HRReviewSnapshotError(
            "Generated regulatory reports cannot be modified"
        )

    review = await build_verified_d5_d6_workflow(
        db,
        company_id=company_id,
        payroll_period_id=report.payroll_period_id,
    )

    verify_review_snapshot(review)

    previous = (
        await db.execute(
            text("""
                SELECT id
                FROM payroll_hr_review_snapshots
                WHERE company_id = :company_id
                  AND payroll_period_id = :period_id
                  AND report_kind = :kind
                  AND report_revision < :revision
                ORDER BY report_revision DESC, id DESC
                LIMIT 1
            """),
            {
                "company_id": company_id,
                "period_id": report.payroll_period_id,
                "kind": report.report_kind,
                "revision": report.revision,
            },
        )
    ).scalar_one_or_none()

    payload = {
        "schema": "HR_REVIEW_SNAPSHOT_V1",
        "identity": {
            "company_id": company_id,
            "payroll_period_id": report.payroll_period_id,
            "payroll_regulatory_report_id": report.id,
            "report_kind": report.report_kind,
            "report_revision": report.revision,
            "previous_snapshot_id": previous,
        },
        "review": review,
        "official_statutory_generation": "BLOCKED",
    }

    validate_stored_snapshot(payload)

    payload_json = canonical(payload)
    checksum = digest(payload)

    existing_id = (
        await db.execute(
            text("""
                SELECT id
                FROM payroll_hr_review_snapshots
                WHERE company_id = :company_id
                  AND payroll_regulatory_report_id = :report_id
            """),
            {
                "company_id": company_id,
                "report_id": report.id,
            },
        )
    ).scalar_one_or_none()

    if existing_id is not None:
        existing = await load_hr_snapshot(
            db,
            company_id=company_id,
            snapshot_id=existing_id,
        )

        if existing["sha256"] != checksum:
            raise HRReviewSnapshotError(
                "Existing snapshot differs from current HR sources; "
                "create a new report revision"
            )

        return existing

    inserted_id = (
        await db.execute(
            text("""
                INSERT INTO payroll_hr_review_snapshots (
                    company_id,
                    payroll_period_id,
                    payroll_regulatory_report_id,
                    report_kind,
                    report_revision,
                    previous_snapshot_id,
                    payload_json,
                    snapshot_sha256
                )
                VALUES (
                    :company_id,
                    :period_id,
                    :report_id,
                    :kind,
                    :revision,
                    :previous_id,
                    :payload,
                    :sha256
                )
                RETURNING id
            """),
            {
                "company_id": company_id,
                "period_id": report.payroll_period_id,
                "report_id": report.id,
                "kind": report.report_kind,
                "revision": report.revision,
                "previous_id": previous,
                "payload": payload_json,
                "sha256": checksum,
            },
        )
    ).scalar_one()

    return await load_hr_snapshot(
        db,
        company_id=company_id,
        snapshot_id=inserted_id,
    )


async def compare_persisted_hr_snapshots(
    db,
    *,
    company_id,
    previous_snapshot_id,
    current_snapshot_id,
):
    old = await load_hr_snapshot(
        db,
        company_id=company_id,
        snapshot_id=previous_snapshot_id,
    )
    new = await load_hr_snapshot(
        db,
        company_id=company_id,
        snapshot_id=current_snapshot_id,
    )

    old_identity = old["payload"]["identity"]
    new_identity = new["payload"]["identity"]

    if (
        old_identity["report_kind"] != new_identity["report_kind"]
        or old_identity["payroll_period_id"]
        != new_identity["payroll_period_id"]
        or old_identity["report_revision"]
        >= new_identity["report_revision"]
    ):
        raise HRReviewSnapshotError(
            "Invalid HR snapshot revision sequence"
        )

    if new["previous_snapshot_id"] != old["id"]:
        raise HRReviewSnapshotError(
            "HR snapshot history chain mismatch"
        )

    return compare_hr_reviews(
        old["payload"]["review"],
        new["payload"]["review"],
    )
