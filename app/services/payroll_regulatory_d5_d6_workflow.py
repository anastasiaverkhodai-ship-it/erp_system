"""D5/D6 internal HR review and correction workflow.

Produces a deterministic evidence snapshot and change comparison
from verified tenant-scoped HR source inventory.

This module does not:
- infer statutory D5 event codes;
- infer D6 applicability without independently reviewed facts;
- approve normative mappings;
- mutate HR records;
- mark regulatory reports as GENERATED;
- create official filing rows.
"""

import hashlib
import json
import re
from copy import deepcopy

from app.services.payroll_regulatory_d5_d6_source_control import (
    SOURCE_TABLES,
    build_d5_d6_source_control,
)


class D5D6WorkflowError(ValueError):
    pass


DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def fail(message):
    raise D5D6WorkflowError(message)


def positive_id(value, field):
    if type(value) is not int or value <= 0:
        fail("Invalid " + field)
    return value


def valid_digest(value):
    return (
        isinstance(value, str)
        and DIGEST_PATTERN.fullmatch(value) is not None
    )


def canonical_json(value):
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )


def digest(value):
    return hashlib.sha256(
        canonical_json(value).encode("utf-8")
    ).hexdigest()


def verify_evidence_item(item, *, company_id, table_name):
    if not isinstance(item, dict):
        fail("Invalid HR evidence item")

    if item.get("company_id") != company_id:
        fail("Cross-company HR evidence")

    if item.get("source_table") != table_name:
        fail("HR source table mismatch")

    source_id = positive_id(
        item.get("source_id"),
        "HR source identifier",
    )

    if not valid_digest(item.get("source_sha256")):
        fail("Invalid HR source digest")

    if item.get("review_only") is not True:
        fail("HR evidence must remain review-only")

    if item.get("statutory_classification") != "NOT_VERIFIED":
        fail("Unapproved statutory classification")

    event_date = item.get("event_date")

    if event_date is not None:
        from datetime import date

        if type(event_date) is not str:
            fail("Invalid HR event date")

        try:
            parsed = date.fromisoformat(event_date)
        except ValueError:
            fail("Invalid HR event date")

        if parsed.isoformat() != event_date:
            fail("Non-canonical HR event date")

        if not isinstance(item.get("date_field"), str):
            fail("Missing HR event date provenance")

    return source_id


def assemble_hr_review(inventory):
    """Validate collected HR evidence and build stable review state."""

    if not isinstance(inventory, dict):
        fail("D5/D6 source inventory required")

    company_id = positive_id(
        inventory.get("company_id"),
        "company",
    )

    period_id = positive_id(
        inventory.get("payroll_period_id"),
        "payroll period",
    )

    if inventory.get("review_only") is not True:
        fail("D5/D6 review-only gate missing")

    if inventory.get("official_statutory_generation") != "BLOCKED":
        fail("Official statutory generation must remain blocked")

    source_inventory = inventory.get("source_inventory")

    if not isinstance(source_inventory, list):
        fail("HR source inventory must be a list")

    source_names = set()
    evidence_keys = set()
    records = []
    ambiguous_count = 0
    dated_count = 0

    for section in source_inventory:
        if not isinstance(section, dict):
            fail("Invalid source inventory section")

        table_name = section.get("source_table")

        if table_name not in SOURCE_TABLES:
            fail("Unsupported HR evidence source")

        if table_name in source_names:
            fail("Duplicate HR source inventory section")

        source_names.add(table_name)

        dated = section.get("dated_evidence")
        undated = section.get("undated_evidence")

        if not isinstance(dated, list) or not isinstance(undated, list):
            fail("Invalid HR evidence groups")

        if section.get("dated_in_period_count") != len(dated):
            fail("Dated HR evidence counter mismatch")

        if section.get("undated_or_ambiguous_count") != len(undated):
            fail("Ambiguous HR evidence counter mismatch")

        outside = section.get("dated_outside_period_count")
        scanned = section.get("scanned_count")

        if (
            type(outside) is not int
            or outside < 0
            or type(scanned) is not int
            or scanned != len(dated) + len(undated) + outside
        ):
            fail("HR source scan reconciliation mismatch")

        for group_name, group in (
            ("DATED", dated),
            ("AMBIGUOUS", undated),
        ):
            for item in group:
                source_id = verify_evidence_item(
                    item,
                    company_id=company_id,
                    table_name=table_name,
                )

                if group_name == "DATED" and item["event_date"] is None:
                    fail("Dated HR evidence has no date")

                if group_name == "AMBIGUOUS" and item["event_date"] is not None:
                    fail("Ambiguous HR evidence contains event date")

                key = (table_name, source_id)

                if key in evidence_keys:
                    fail("Duplicate HR evidence across groups")

                evidence_keys.add(key)

                records.append({
                    "source_table": table_name,
                    "source_id": source_id,
                    "source_sha256": item["source_sha256"],
                    "event_date": item["event_date"],
                    "date_field": item.get("date_field"),
                    "evidence_state": group_name,
                    "statutory_classification": "NOT_VERIFIED",
                })

        dated_count += len(dated)
        ambiguous_count += len(undated)

    if inventory.get("dated_evidence_count") != dated_count:
        fail("Aggregate dated evidence mismatch")

    if inventory.get("ambiguous_evidence_count") != ambiguous_count:
        fail("Aggregate ambiguous evidence mismatch")

    missing = inventory.get("missing_source_tables")

    if not isinstance(missing, list):
        fail("Missing source table inventory required")

    if (
        len(missing) != len(set(missing))
        or any(name not in SOURCE_TABLES for name in missing)
        or set(missing).intersection(source_names)
        or set(missing).union(source_names) != set(SOURCE_TABLES)
    ):
        fail("Inconsistent HR source table coverage")

    records.sort(
        key=lambda record: (
            record["source_table"],
            record["source_id"],
        )
    )

    snapshot = {
        "company_id": company_id,
        "payroll_period_id": period_id,
        "period_start": inventory.get("period_start"),
        "period_end": inventory.get("period_end"),
        "records": records,
        "missing_source_tables": sorted(missing),
        "dated_evidence_count": dated_count,
        "ambiguous_evidence_count": ambiguous_count,
        "review_only": True,
    }

    snapshot_hash = digest(snapshot)

    return {
        "company_id": company_id,
        "payroll_period_id": period_id,
        "review_only": True,
        "snapshot": snapshot,
        "snapshot_sha256": snapshot_hash,
        "source_record_count": len(records),
        "d5": {
            "evidence_collection": "VERIFIED",
            "candidate_event_count": dated_count,
            "ambiguous_event_count": ambiguous_count,
            "statutory_event_mapping": "NOT_APPROVED",
            "official_rows_generated": 0,
        },
        "d6": {
            "source_inventory": "AVAILABLE_FOR_REVIEW",
            "applicability": "NOT_DETERMINED",
            "normative_mapping": "NOT_APPROVED",
            "official_rows_generated": 0,
        },
        "overall_status": "REVIEW_ONLY",
        "official_statutory_generation": "BLOCKED",
    }


def verify_review_snapshot(review):
    if not isinstance(review, dict):
        fail("Review snapshot required")

    snapshot = review.get("snapshot")

    if not isinstance(snapshot, dict):
        fail("Missing HR snapshot")

    if not valid_digest(review.get("snapshot_sha256")):
        fail("Invalid HR snapshot digest")

    if digest(snapshot) != review["snapshot_sha256"]:
        fail("HR snapshot content changed")

    if snapshot.get("company_id") != review.get("company_id"):
        fail("Snapshot company mismatch")

    if snapshot.get("payroll_period_id") != review.get(
        "payroll_period_id"
    ):
        fail("Snapshot payroll period mismatch")

    if review.get("review_only") is not True:
        fail("Snapshot must remain review-only")

    if review.get("official_statutory_generation") != "BLOCKED":
        fail("Official generation must remain blocked")

    return True


def compare_hr_reviews(before, after):
    """Build proposed corrections by comparing two verified snapshots.

    This is a review diff, not an applied statutory correction.
    """

    verify_review_snapshot(before)
    verify_review_snapshot(after)

    if (
        before["company_id"] != after["company_id"]
        or before["payroll_period_id"] != after["payroll_period_id"]
    ):
        fail("Cross-period or cross-company correction comparison")

    def index(review):
        result = {}

        for item in review["snapshot"]["records"]:
            key = (
                item["source_table"],
                item["source_id"],
            )

            if key in result:
                fail("Duplicate source in correction snapshot")

            result[key] = item

        return result

    previous = index(before)
    current = index(after)

    changes = []

    for key in sorted(set(previous).union(current)):
        old = previous.get(key)
        new = current.get(key)

        if old is None:
            kind = "ADDED_SOURCE"
        elif new is None:
            kind = "REMOVED_SOURCE"
        elif old != new:
            kind = "CHANGED_SOURCE"
        else:
            continue

        changes.append({
            "change_type": kind,
            "source_table": key[0],
            "source_id": key[1],
            "previous_source_sha256": (
                old["source_sha256"] if old else None
            ),
            "current_source_sha256": (
                new["source_sha256"] if new else None
            ),
            "requires_hr_review": True,
            "statutory_correction_approved": False,
        })

    return {
        "company_id": before["company_id"],
        "payroll_period_id": before["payroll_period_id"],
        "before_snapshot_sha256": before["snapshot_sha256"],
        "after_snapshot_sha256": after["snapshot_sha256"],
        "change_count": len(changes),
        "changes": changes,
        "correction_status": "REVIEW_REQUIRED",
        "statutory_correction_generated": False,
        "official_statutory_generation": "BLOCKED",
    }


async def build_verified_d5_d6_workflow(
    db,
    *,
    company_id,
    payroll_period_id,
):
    inventory = await build_d5_d6_source_control(
        db,
        company_id=company_id,
        payroll_period_id=payroll_period_id,
    )

    review = assemble_hr_review(inventory)
    from app.services.payroll_d6_evidence_service import period_evidence
    evidence = await period_evidence(db, company_id=company_id, payroll_period_id=payroll_period_id)
    # Bind approved facts and their review provenance into the existing immutable
    # snapshot and source-diff mechanism. This never approves statutory mapping.
    review["snapshot"]["d6_evidence"] = evidence
    for record in evidence["records"]:
        review["snapshot"]["records"].append({
            "source_table": "payroll_d6_evidence",
            "source_id": record["evidence_id"],
            "source_sha256": digest(record),
            "event_date": None,
            "date_field": None,
            "evidence_state": "INDEPENDENTLY_REVIEWED",
            "statutory_classification": "NOT_VERIFIED",
        })
    review["snapshot_sha256"] = digest(review["snapshot"])
    review["source_record_count"] = len(review["snapshot"]["records"])
    review["d6"].update(applicability=evidence["applicability"],
        source_coverage=evidence["coverage"], missing_contract_ids=evidence["missing_contract_ids"])
    return review
