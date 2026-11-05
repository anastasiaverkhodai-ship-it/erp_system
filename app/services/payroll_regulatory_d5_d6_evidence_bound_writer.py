"""D5/D6 HR inventory evidence inside immutable internal reviews.

Binds a verified HR source-inventory fingerprint to both D5
and D6 rows. This does NOT approve D5 statutory event mapping,
D6 applicability, worktime mapping, or official generation.

The existing HR workflow is the independent source of inventory
and digest validation. Caller owns the transaction.
"""

import hashlib
import hmac
import json
import re

from sqlalchemy import text

from app.services.payroll_regulatory_d5_d6_workflow import (
    build_verified_d5_d6_workflow,
    verify_review_snapshot,
)

from app.services.payroll_regulatory_verified_gross_writer import (
    persist_gross_verified_internal_four_form_review,
)

from app.services.payroll_regulatory_internal_reconciliation import (
    InternalSnapshot,
    verify_internal_snapshot,
)


KEY = "hr_inventory_evidence"
SCHEMA = "erp.d5-d6-hr-inventory.v1"
DIGEST = re.compile(r"[0-9a-f]{64}\Z")


class HRInventoryBindingError(ValueError):
    pass


def canonical(value):
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def make_anchor(review):
    if not isinstance(review, dict):
        raise HRInventoryBindingError("HR review missing")

    if (
        review.get("review_only") is not True
        or review.get("overall_status") != "REVIEW_ONLY"
        or review.get("official_statutory_generation") != "BLOCKED"
    ):
        raise HRInventoryBindingError(
            "Unsafe HR review state"
        )

    d5 = review.get("d5")
    d6 = review.get("d6")

    if not isinstance(d5, dict) or not isinstance(d6, dict):
        raise HRInventoryBindingError(
            "D5/D6 review evidence missing"
        )

    if (
        d5.get("evidence_collection") != "VERIFIED"
        or d5.get("statutory_event_mapping") != "NOT_APPROVED"
        or d5.get("official_rows_generated") != 0
        or d6.get("applicability") != "NOT_DETERMINED"
        or d6.get("normative_mapping") != "NOT_APPROVED"
        or d6.get("official_rows_generated") != 0
    ):
        raise HRInventoryBindingError(
            "Unapproved normative HR evidence"
        )

    source_hash = review.get("snapshot_sha256")
    count = review.get("source_record_count")

    if (
        not isinstance(source_hash, str)
        or DIGEST.fullmatch(source_hash) is None
        or type(count) is not int
        or count < 0
    ):
        raise HRInventoryBindingError(
            "Invalid HR source fingerprint"
        )

    company = review.get("company_id")
    period = review.get("payroll_period_id")

    if (
        type(company) is not int or company <= 0
        or type(period) is not int or period <= 0
    ):
        raise HRInventoryBindingError(
            "Invalid HR tenant or period"
        )

    body = {
        "v": SCHEMA,
        "company": company,
        "period": period,
        "source_sha256": source_hash,
        "source_count": count,
        "d5": "REVIEW_ONLY",
        "d6": "APPLICABILITY_NOT_DETERMINED",
        "official": "BLOCKED",
    }

    digest = hashlib.sha256(
        canonical(body).encode("utf-8")
    ).hexdigest()

    return canonical({**body, "sha256": digest})


def verify_anchor(value):
    if not isinstance(value, str) or not value:
        raise HRInventoryBindingError(
            "HR evidence string missing"
        )

    try:
        evidence = json.loads(value)
    except (ValueError, TypeError):
        raise HRInventoryBindingError(
            "Invalid HR evidence JSON"
        ) from None

    required = {
        "v", "company", "period", "source_sha256",
        "source_count", "d5", "d6", "official", "sha256",
    }

    if (
        not isinstance(evidence, dict)
        or set(evidence) != required
        or canonical(evidence) != value
    ):
        raise HRInventoryBindingError(
            "Noncanonical HR evidence"
        )

    if (
        evidence["v"] != SCHEMA
        or evidence["d5"] != "REVIEW_ONLY"
        or evidence["d6"] != "APPLICABILITY_NOT_DETERMINED"
        or evidence["official"] != "BLOCKED"
        or type(evidence["company"]) is not int
        or evidence["company"] <= 0
        or type(evidence["period"]) is not int
        or evidence["period"] <= 0
        or type(evidence["source_count"]) is not int
        or evidence["source_count"] < 0
        or not isinstance(evidence["source_sha256"], str)
        or DIGEST.fullmatch(evidence["source_sha256"]) is None
    ):
        raise HRInventoryBindingError(
            "Invalid HR evidence state"
        )

    body = {
        k: v for k, v in evidence.items()
        if k != "sha256"
    }

    expected = hashlib.sha256(
        canonical(body).encode("utf-8")
    ).hexdigest()

    if (
        not isinstance(evidence["sha256"], str)
        or not hmac.compare_digest(
            evidence["sha256"], expected
        )
    ):
        raise HRInventoryBindingError(
            "HR evidence digest mismatch"
        )

    return evidence


async def bind_d5_d6_hr_evidence(
    db,
    *,
    company_id,
    payroll_period_id,
    payroll_period,
    report_references,
):
    """Bind one independently verified HR inventory to both forms."""

    if set(report_references) != {"D1", "4DF", "D5", "D6"}:
        raise HRInventoryBindingError(
            "All four report references required"
        )

    review = await build_verified_d5_d6_workflow(
        db,
        company_id=company_id,
        payroll_period_id=payroll_period_id,
    )

    if verify_review_snapshot(review) is not True:
        raise HRInventoryBindingError(
            "HR source review integrity failed"
        )

    anchor = make_anchor(review)
    checked = verify_anchor(anchor)

    if (
        checked["company"] != company_id
        or checked["period"] != payroll_period_id
    ):
        raise HRInventoryBindingError(
            "HR review tenant mismatch"
        )

    prepared = []

    for form, kind in (("D5", "d5"), ("D6", "d6")):
        report_id = report_references[form]

        header = (
            await db.execute(
                text("""
                    SELECT id, status, report_kind
                    FROM payroll_regulatory_reports
                    WHERE company_id = :cid
                      AND payroll_period_id = :pid
                      AND id = :rid
                    FOR UPDATE
                """),
                {
                    "cid": company_id,
                    "pid": payroll_period_id,
                    "rid": report_id,
                },
            )
        ).mappings().one_or_none()

        if (
            header is None
            or header["status"] != "draft"
            or header["report_kind"] != kind
        ):
            raise HRInventoryBindingError(
                f"{form}: incorrect draft report scope"
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
                {"cid": company_id, "rid": report_id},
            )
        ).mappings().all()

        if not rows:
            raise HRInventoryBindingError(
                f"{form}: internal evidence rows required"
            )

        for row in rows:
            if row["row_code"] != (
                "ERP_INTERNAL_RECONCILIATION_V1"
            ):
                raise HRInventoryBindingError(
                    f"{form}: unsupported row type"
                )

            try:
                payload = json.loads(row["payload_json"])
            except (TypeError, ValueError):
                raise HRInventoryBindingError(
                    f"{form}: malformed row"
                ) from None

            if (
                not isinstance(payload, dict)
                or payload.get("schema")
                    != "erp.internal-regulatory-row.v1"
                or payload.get("form") != form
                or payload.get("company_id") != company_id
                or payload.get("payroll_period") != payroll_period
            ):
                raise HRInventoryBindingError(
                    f"{form}: incorrect source envelope"
                )

            attrs = payload.get("attributes")

            if not isinstance(attrs, dict):
                raise HRInventoryBindingError(
                    f"{form}: invalid attributes"
                )

            previous = attrs.get(KEY)

            if previous is not None:
                verify_anchor(previous)

                if previous != anchor:
                    raise HRInventoryBindingError(
                        f"{form}: existing evidence differs"
                    )

            updated = dict(payload)
            updated_attrs = dict(attrs)
            updated_attrs[KEY] = anchor
            updated["attributes"] = updated_attrs

            prepared.append(
                (row["id"], report_id, canonical(updated))
            )

    # Both forms are validated before any row update.
    for row_id, report_id, payload in prepared:
        await db.execute(
            text("""
                UPDATE payroll_regulatory_report_rows
                SET payload_json = :payload
                WHERE company_id = :cid
                  AND payroll_regulatory_report_id = :rid
                  AND id = :row_id
            """),
            {
                "cid": company_id,
                "rid": report_id,
                "row_id": row_id,
                "payload": payload,
            },
        )

    return anchor


async def persist_d5_d6_evidence_bound_review(
    db,
    *,
    company_id,
    payroll_period_id,
    payroll_period,
    report_references,
    request_key,
):
    """Persist and re-read HR evidence from canonical snapshot."""

    anchor = await bind_d5_d6_hr_evidence(
        db,
        company_id=company_id,
        payroll_period_id=payroll_period_id,
        payroll_period=payroll_period,
        report_references=report_references,
    )

    result = await persist_gross_verified_internal_four_form_review(
        db,
        company_id=company_id,
        payroll_period_id=payroll_period_id,
        payroll_period=payroll_period,
        report_references=report_references,
        request_key=request_key,
    )

    stored = (
        await db.execute(
            text("""
                SELECT version, payload_json, snapshot_sha256
                FROM payroll_regulatory_internal_snapshots
                WHERE company_id = :cid
                  AND payroll_period_id = :pid
                  AND id = :sid
            """),
            {
                "cid": company_id,
                "pid": payroll_period_id,
                "sid": result["id"],
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

    content = verify_internal_snapshot(snapshot)

    for form in ("D5", "D6"):
        rows = [
            row for row in content["rows"]
            if row["form"] == form
        ]

        if not rows:
            raise HRInventoryBindingError(
                f"{form}: missing persisted evidence"
            )

        for row in rows:
            attrs = row.get("attributes")

            if not isinstance(attrs, dict):
                raise HRInventoryBindingError(
                    f"{form}: missing attributes"
                )

            actual = attrs.get(KEY)
            verify_anchor(actual)

            if actual != anchor:
                raise HRInventoryBindingError(
                    f"{form}: persisted evidence mismatch"
                )

    if result.get("official_generation") != "BLOCKED":
        raise HRInventoryBindingError(
            "Official generation must remain blocked"
        )

    return {
        **result,
        "hr_inventory_sha256": verify_anchor(anchor)[
            "source_sha256"
        ],
        "d5_d6_hr_inventory_bound": True,
        "d5_statutory_mapping_verified": False,
        "d6_applicability_verified": False,
        "official_generation": "BLOCKED",
    }
