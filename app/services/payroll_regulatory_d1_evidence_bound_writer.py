"""Immutable D1 aggregate-evidence binding for internal reviews.

This module anchors independently verified D1 aggregate controls
inside the canonical four-form internal snapshot.

It does not approve separate PIT/military levy amounts,
payment evidence, D5/D6 applicability, or official filing.

Caller owns the transaction. No production migrations are needed.
"""

import hashlib
import json

from sqlalchemy import text

from app.services.payroll_regulatory_d1_review_projection import (
    build_verified_d1_review,
)

from app.services.payroll_regulatory_d1_tax_controlled_writer import (
    validate_d1_source_control,
    persist_d1_tax_controlled_internal_review,
)

from app.services.payroll_regulatory_verified_gross_writer import (
    derive_independent_gross_controls,
)

from app.services.payroll_regulatory_internal_reconciliation import (
    InternalSnapshot,
    verify_internal_snapshot,
)


ANCHOR_KEY = "d1_aggregate_evidence"
ANCHOR_SCHEMA = "erp.d1-aggregate-evidence.v1"


class D1EvidenceBindingError(ValueError):
    pass


def canonical(value):
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def make_anchor(control):
    if not isinstance(control, dict):
        raise D1EvidenceBindingError(
            "Verified D1 control required"
        )

    if (
        control.get("d1_source_review_checked") is not True
        or control.get("gross_independently_reconciled") is not True
        or control.get("official_generation") != "BLOCKED"
        or control.get("separate_pit_verified") is not False
        or control.get("separate_military_levy_verified") is not False
    ):
        raise D1EvidenceBindingError(
            "Unapproved D1 source state"
        )

    evidence = {
        "schema": ANCHOR_SCHEMA,
        "control": control,
    }

    digest = hashlib.sha256(
        canonical(evidence).encode("utf-8")
    ).hexdigest()

    return {
        **evidence,
        "sha256": digest,
    }


def serialize_anchor(anchor):
    """Canonical bounded string for persisted row attributes."""

    verify_anchor(anchor)

    serialized = canonical(anchor)

    if not isinstance(serialized, str) or not serialized:
        raise D1EvidenceBindingError(
            "Empty serialized D1 evidence"
        )

    return serialized


def deserialize_anchor(value):
    """Fail closed on malformed/noncanonical evidence strings."""

    if not isinstance(value, str) or not value:
        raise D1EvidenceBindingError(
            "Persisted D1 evidence must be a nonempty string"
        )

    try:
        anchor = json.loads(value)
    except (ValueError, TypeError):
        raise D1EvidenceBindingError(
            "Malformed persisted D1 evidence JSON"
        ) from None

    verify_anchor(anchor)

    if canonical(anchor) != value:
        raise D1EvidenceBindingError(
            "Noncanonical persisted D1 evidence"
        )

    return anchor


def verify_anchor(anchor):
    if not isinstance(anchor, dict):
        raise D1EvidenceBindingError(
            "D1 evidence anchor missing"
        )

    if set(anchor) != {"schema", "control", "sha256"}:
        raise D1EvidenceBindingError(
            "Unexpected D1 evidence structure"
        )

    if anchor["schema"] != ANCHOR_SCHEMA:
        raise D1EvidenceBindingError(
            "Invalid D1 evidence schema"
        )

    expected = make_anchor(anchor["control"])

    if anchor != expected:
        raise D1EvidenceBindingError(
            "D1 evidence integrity mismatch"
        )

    return True


async def bind_d1_aggregate_evidence(
    db,
    *,
    company_id,
    payroll_period_id,
    payroll_period,
    report_references,
):
    """Derive aggregate evidence from persisted sources.

    Only draft report rows may be enriched. Existing anchors
    must match the current verified control byte-for-byte.
    """

    gross = await derive_independent_gross_controls(
        db,
        company_id=company_id,
        payroll_period_id=payroll_period_id,
        payroll_period=payroll_period,
        report_references=report_references,
    )

    review = await build_verified_d1_review(
        db,
        company_id=company_id,
        payroll_period_id=payroll_period_id,
    )

    control = validate_d1_source_control(
        review,
        company_id=company_id,
        payroll_period_id=payroll_period_id,
        independent_gross=gross["D1"]["gross"],
    )

    anchor = make_anchor(control)
    anchor_string = serialize_anchor(anchor)
    report_id = report_references["D1"]

    header = (
        await db.execute(
            text("""
                SELECT id, status, report_kind
                FROM payroll_regulatory_reports
                WHERE id = :rid
                  AND company_id = :cid
                  AND payroll_period_id = :pid
                FOR UPDATE
            """),
            {
                "rid": report_id,
                "cid": company_id,
                "pid": payroll_period_id,
            },
        )
    ).mappings().one_or_none()

    if (
        header is None
        or header["status"] != "draft"
        or header["report_kind"] != "d1"
    ):
        raise D1EvidenceBindingError(
            "D1 report is not a scoped draft"
        )

    rows = (
        await db.execute(
            text("""
                SELECT id, row_code, payload_json
                FROM payroll_regulatory_report_rows
                WHERE company_id = :cid
                  AND payroll_regulatory_report_id = :rid
                ORDER BY row_no
                FOR UPDATE
            """),
            {
                "cid": company_id,
                "rid": report_id,
            },
        )
    ).mappings().all()

    if not rows:
        raise D1EvidenceBindingError(
            "D1 report has no source rows"
        )

    prepared = []

    # Validate ALL rows before modifying ANY row.
    for row in rows:
        if row["row_code"] != (
            "ERP_INTERNAL_RECONCILIATION_V1"
        ):
            raise D1EvidenceBindingError(
                "Unsupported internal D1 row"
            )

        try:
            payload = json.loads(row["payload_json"])
        except (TypeError, ValueError):
            raise D1EvidenceBindingError(
                "Malformed D1 row payload"
            ) from None

        if not isinstance(payload, dict):
            raise D1EvidenceBindingError(
                "D1 row must contain an object"
            )

        if (
            payload.get("schema")
                != "erp.internal-regulatory-row.v1"
            or payload.get("form") != "D1"
            or payload.get("company_id") != company_id
            or payload.get("payroll_period")
                != payroll_period
        ):
            raise D1EvidenceBindingError(
                "D1 source envelope mismatch"
            )

        attributes = payload.get("attributes")

        if not isinstance(attributes, dict):
            raise D1EvidenceBindingError(
                "Invalid D1 row attributes"
            )

        previous = attributes.get(ANCHOR_KEY)

        if previous is not None:
            previous_anchor = deserialize_anchor(previous)

            if previous_anchor != anchor:
                raise D1EvidenceBindingError(
                    "Current D1 sources differ from existing anchor"
                )

        updated_attributes = dict(attributes)
        updated_attributes[ANCHOR_KEY] = anchor_string

        updated_payload = dict(payload)
        updated_payload["attributes"] = updated_attributes

        prepared.append(
            (row["id"], canonical(updated_payload))
        )

    for row_id, payload_json in prepared:
        await db.execute(
            text("""
                UPDATE payroll_regulatory_report_rows
                SET payload_json = :payload
                WHERE id = :row_id
                  AND company_id = :cid
                  AND payroll_regulatory_report_id = :rid
            """),
            {
                "payload": payload_json,
                "row_id": row_id,
                "cid": company_id,
                "rid": report_id,
            },
        )

    return anchor


async def persist_d1_evidence_bound_review(
    db,
    *,
    company_id,
    payroll_period_id,
    payroll_period,
    report_references,
    request_key,
):
    """Persist only when canonical snapshot contains D1 evidence."""

    anchor = await bind_d1_aggregate_evidence(
        db,
        company_id=company_id,
        payroll_period_id=payroll_period_id,
        payroll_period=payroll_period,
        report_references=report_references,
    )

    result = await persist_d1_tax_controlled_internal_review(
        db,
        company_id=company_id,
        payroll_period_id=payroll_period_id,
        payroll_period=payroll_period,
        report_references=report_references,
        request_key=request_key,
    )

    if result.get("official_generation") != "BLOCKED":
        raise D1EvidenceBindingError(
            "Official generation unexpectedly authorized"
        )

    stored = (
        await db.execute(
            text("""
                SELECT version, payload_json, snapshot_sha256
                FROM payroll_regulatory_internal_snapshots
                WHERE id = :sid
                  AND company_id = :cid
                  AND payroll_period_id = :pid
            """),
            {
                "sid": result["id"],
                "cid": company_id,
                "pid": payroll_period_id,
            },
        )
    ).mappings().one()

    snapshot = InternalSnapshot(
        company_id=company_id,
        payroll_period=payroll_period,
        version=stored["version"],
        canonical_json=stored["payload_json"],
        sha256=stored["snapshot_sha256"].strip(),
    )

    payload = verify_internal_snapshot(snapshot)

    d1_rows = [
        row
        for row in payload["rows"]
        if row["form"] == "D1"
    ]

    if not d1_rows:
        raise D1EvidenceBindingError(
            "Persisted snapshot is missing D1"
        )

    for row in d1_rows:
        attributes = row.get("attributes")

        if not isinstance(attributes, dict):
            raise D1EvidenceBindingError(
                "Persisted D1 attributes missing"
            )

        actual = attributes.get(ANCHOR_KEY)
        actual_anchor = deserialize_anchor(actual)

        if actual_anchor != anchor:
            raise D1EvidenceBindingError(
                "Persisted D1 evidence binding mismatch"
            )

    return {
        **result,
        "d1_aggregate_evidence_sha256": anchor["sha256"],
        "d1_aggregate_evidence_in_snapshot": True,
        "separate_pit_verified": False,
        "separate_military_levy_verified": False,
        "payment_evidence_verified": False,
        "hr_sources_verified": False,
        "official_generation": "BLOCKED",
    }
