"""Atomic append-only persistence of four-form internal reviews.

Caller owns the transaction. No commit, statutory generation,
regulatory approval, journal posting, or submission occurs here.
"""

import hashlib
import json

from sqlalchemy import text

from app.services.payroll_regulatory_persisted_internal_bridge import (
    DATABASE_KINDS,
    review_persisted_four_form_reports,
)
from app.services.payroll_regulatory_internal_reconciliation import (
    verify_internal_snapshot,
)


class InternalRegulatorySnapshotWriteError(ValueError):
    pass


def _positive(value, name):
    if type(value) is not int or value <= 0:
        raise InternalRegulatorySnapshotWriteError(
            f"Invalid {name}"
        )
    return value


async def persist_internal_four_form_review(
    db,
    *,
    company_id,
    payroll_period_id,
    payroll_period,
    report_references,
    expected_source_totals,
    request_key,
):
    """Persist a verified *internal* review with exact replay.

    expected_source_totals is an externally supplied control.
    It must be separately derived from independently verified
    payroll records before calling this service.

    Persisting this evidence does not establish that independence.
    """

    _positive(company_id, "company_id")
    _positive(payroll_period_id, "payroll_period_id")

    if (
        not isinstance(request_key, str)
        or not 8 <= len(request_key.strip()) <= 120
    ):
        raise InternalRegulatorySnapshotWriteError(
            "Valid request key required"
        )

    key = request_key.strip()

    if set(report_references) != set(DATABASE_KINDS):
        raise InternalRegulatorySnapshotWriteError(
            "Exactly four report references required"
        )

    ids = [
        _positive(report_references[form], form)
        for form in DATABASE_KINDS
    ]

    if len(set(ids)) != 4:
        raise InternalRegulatorySnapshotWriteError(
            "Report references must be distinct"
        )

    # The period lock serializes writers for the same
    # company and month without changing source records.
    locked = (
        await db.execute(
            text("""
                SELECT id
                FROM payroll_periods
                WHERE company_id = :company_id
                  AND id = :period_id
                FOR UPDATE
            """),
            {
                "company_id": company_id,
                "period_id": payroll_period_id,
            },
        )
    ).scalar_one_or_none()

    if locked != payroll_period_id:
        raise InternalRegulatorySnapshotWriteError(
            "Payroll period not found"
        )

    # Lock the actual report headers while materializing the review.
    locked_reports = (
        await db.execute(
            text("""
                SELECT id, report_kind, status,
                       payroll_period_id
                FROM payroll_regulatory_reports
                WHERE company_id = :company_id
                  AND id IN (:d1, :fourdf, :d5, :d6)
                ORDER BY id
                FOR UPDATE
            """),
            {
                "company_id": company_id,
                "d1": report_references["D1"],
                "fourdf": report_references["4DF"],
                "d5": report_references["D5"],
                "d6": report_references["D6"],
            },
        )
    ).mappings().all()

    if len(locked_reports) != 4:
        raise InternalRegulatorySnapshotWriteError(
            "Missing report headers"
        )

    expected_kinds = {
        report_references[form]: DATABASE_KINDS[form]
        for form in DATABASE_KINDS
    }

    for report in locked_reports:
        if (
            report["report_kind"] != expected_kinds[report["id"]]
            or report["status"] != "draft"
            or report["payroll_period_id"] != payroll_period_id
        ):
            raise InternalRegulatorySnapshotWriteError(
                "Report scope or draft status mismatch"
            )

    latest = (
        await db.execute(
            text("""
                SELECT id, version, payload_json,
                       snapshot_sha256
                FROM payroll_regulatory_internal_snapshots
                WHERE company_id = :company_id
                  AND payroll_period_id = :period_id
                ORDER BY version DESC
                LIMIT 1
            """),
            {
                "company_id": company_id,
                "period_id": payroll_period_id,
            },
        )
    ).mappings().one_or_none()

    # Replay is checked against freshly reconstructed evidence.
    # A changed source must not reuse the same immutable record.
    previous = None

    if latest is not None:
        from app.services.payroll_regulatory_internal_reconciliation import (
            InternalSnapshot,
        )

        previous = InternalSnapshot(
            company_id=company_id,
            payroll_period=payroll_period,
            version=latest["version"],
            canonical_json=latest["payload_json"],
            sha256=latest["snapshot_sha256"].strip(),
        )

        verify_internal_snapshot(previous)

    existing = (
        await db.execute(
            text("""
                SELECT id, version, payload_json,
                       snapshot_sha256,
                       d1_report_id, fourdf_report_id,
                       d5_report_id, d6_report_id
                FROM payroll_regulatory_internal_snapshots
                WHERE company_id = :company_id
                  AND payroll_period_id = :period_id
                  AND request_key = :request_key
            """),
            {
                "company_id": company_id,
                "period_id": payroll_period_id,
                "request_key": key,
            },
        )
    ).mappings().one_or_none()

    if existing is not None:
        # The stored version has to match its current sources.
        from app.services.payroll_regulatory_internal_reconciliation import (
            InternalSnapshot,
        )

        stored = InternalSnapshot(
            company_id=company_id,
            payroll_period=payroll_period,
            version=existing["version"],
            canonical_json=existing["payload_json"],
            sha256=existing["snapshot_sha256"].strip(),
        )

        verify_internal_snapshot(stored)

        if any((
            existing["d1_report_id"] != report_references["D1"],
            existing["fourdf_report_id"] != report_references["4DF"],
            existing["d5_report_id"] != report_references["D5"],
            existing["d6_report_id"] != report_references["D6"],
        )):
            raise InternalRegulatorySnapshotWriteError(
                "Idempotency key reused for different reports"
            )

        # Reconstruct the same version against current report rows.
        candidate = await review_persisted_four_form_reports(
            db,
            company_id=company_id,
            payroll_period_id=payroll_period_id,
            payroll_period=payroll_period,
            report_references=report_references,
            expected_source_totals=expected_source_totals,
            version=existing["version"],
            previous=None,
        ) if existing["version"] == 1 else None

        # A noninitial version requires its own predecessor.
        if candidate is None:
            prior = (
                await db.execute(
                    text("""
                        SELECT payload_json, snapshot_sha256,
                               version
                        FROM payroll_regulatory_internal_snapshots
                        WHERE company_id = :company_id
                          AND payroll_period_id = :period_id
                          AND version = :version
                    """),
                    {
                        "company_id": company_id,
                        "period_id": payroll_period_id,
                        "version": existing["version"] - 1,
                    },
                )
            ).mappings().one_or_none()

            if prior is None:
                raise InternalRegulatorySnapshotWriteError(
                    "Missing replay predecessor"
                )

            prior_snapshot = InternalSnapshot(
                company_id=company_id,
                payroll_period=payroll_period,
                version=prior["version"],
                canonical_json=prior["payload_json"],
                sha256=prior["snapshot_sha256"].strip(),
            )

            verify_internal_snapshot(prior_snapshot)

            candidate = await review_persisted_four_form_reports(
                db,
                company_id=company_id,
                payroll_period_id=payroll_period_id,
                payroll_period=payroll_period,
                report_references=report_references,
                expected_source_totals=expected_source_totals,
                version=existing["version"],
                previous=prior_snapshot,
            )

        if candidate.sha256 != stored.sha256:
            raise InternalRegulatorySnapshotWriteError(
                "Changed evidence cannot replay immutable snapshot"
            )

        return {
            "id": existing["id"],
            "version": stored.version,
            "sha256": stored.sha256,
            "created": False,
            "official_generation": "BLOCKED",
        }

    version = 1 if latest is None else latest["version"] + 1

    snapshot = await review_persisted_four_form_reports(
        db,
        company_id=company_id,
        payroll_period_id=payroll_period_id,
        payroll_period=payroll_period,
        report_references=report_references,
        expected_source_totals=expected_source_totals,
        version=version,
        previous=previous,
    )

    payload = verify_internal_snapshot(snapshot)

    if any((
        payload["official_generation_authorized"] is not False,
        payload["statutory_mapping_verified"] is not False,
        payload["regulator_accepted"] is not False,
    )):
        raise InternalRegulatorySnapshotWriteError(
            "Internal evidence cannot authorize filing"
        )

    inserted = (
        await db.execute(
            text("""
                INSERT INTO payroll_regulatory_internal_snapshots (
                    company_id, payroll_period_id, version,
                    request_key,
                    d1_report_id, fourdf_report_id,
                    d5_report_id, d6_report_id,
                    previous_snapshot_id,
                    payload_json, snapshot_sha256
                )
                VALUES (
                    :company_id, :period_id, :version,
                    :request_key,
                    :d1, :fourdf, :d5, :d6,
                    :previous_id,
                    :payload, :sha256
                )
                RETURNING id
            """),
            {
                "company_id": company_id,
                "period_id": payroll_period_id,
                "version": version,
                "request_key": key,
                "d1": report_references["D1"],
                "fourdf": report_references["4DF"],
                "d5": report_references["D5"],
                "d6": report_references["D6"],
                "previous_id": (
                    latest["id"] if latest else None
                ),
                "payload": snapshot.canonical_json,
                "sha256": snapshot.sha256,
            },
        )
    ).scalar_one()

    return {
        "id": inserted,
        "version": version,
        "sha256": snapshot.sha256,
        "created": True,
        "official_generation": "BLOCKED",
    }
