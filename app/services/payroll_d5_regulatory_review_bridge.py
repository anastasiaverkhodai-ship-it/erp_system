"""Read-only bridge between D5 source reviews and D5/D6 workflow.

This never authorizes, generates, or submits statutory rows.
"""

import hashlib
import json

from app.services.payroll_d5_verified_review_snapshot import (
    build_d5_review_snapshot,
    verify_d5_review_snapshot,
)
from app.services.payroll_regulatory_d5_d6_workflow import (
    build_verified_d5_d6_workflow,
    verify_review_snapshot,
)


class D5RegulatoryBridgeError(ValueError):
    pass


SCHEMA = "D5_REGULATORY_REVIEW_BRIDGE_V1"


def _positive(value, name):
    if type(value) is not int or value <= 0:
        raise D5RegulatoryBridgeError("Invalid " + name)


def _digest(body):
    return hashlib.sha256(
        json.dumps(
            body,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _workflow_digest(workflow):
    if not isinstance(workflow, dict):
        raise D5RegulatoryBridgeError(
            "Invalid regulatory review structure"
        )

    digest = workflow.get("snapshot_sha256")

    if (
        not isinstance(digest, str)
        or len(digest) != 64
        or any(c not in "0123456789abcdef" for c in digest)
    ):
        raise D5RegulatoryBridgeError(
            "Regulatory review snapshot digest missing"
        )

    if verify_review_snapshot(workflow) is not True:
        raise D5RegulatoryBridgeError(
            "Regulatory review verification failed"
        )

    return digest


async def build_d5_regulatory_review_bridge(
    db,
    *,
    company_id,
    payroll_period_id,
    targets,
):
    """Verify both review systems against the same DB session."""

    _positive(company_id, "company_id")
    _positive(payroll_period_id, "payroll_period_id")

    try:
        workflow = await build_verified_d5_d6_workflow(
            db,
            company_id=company_id,
            payroll_period_id=payroll_period_id,
        )

        regulatory_digest = _workflow_digest(workflow)

        source_snapshot = await build_d5_review_snapshot(
            db,
            company_id=company_id,
            targets=targets,
        )

        if await verify_d5_review_snapshot(
            db, source_snapshot
        ) is not True:
            raise D5RegulatoryBridgeError(
                "D5 source review not verified"
            )
    except Exception as exc:
        raise D5RegulatoryBridgeError(
            "Regulatory/source review verification rejected"
        ) from exc

    source_body = source_snapshot["body"]

    if (
        source_body["official_generation"] != "BLOCKED"
        or source_body["official_rows_generated"] != 0
        or source_body["review_only"] is not True
    ):
        raise D5RegulatoryBridgeError(
            "D5 candidate unexpectedly enabled"
        )

    body = {
        "schema": SCHEMA,
        "company_id": company_id,
        "payroll_period_id": payroll_period_id,
        "regulatory_review_sha256": regulatory_digest,
        "d5_source_snapshot": source_snapshot,
        "d5_target_count": source_body["entry_count"],
        "review_only": True,
        "normative_approval": False,
        "official_generation": "BLOCKED",
        "official_rows_generated": 0,
    }

    return {
        "body": body,
        "bridge_sha256": _digest(body),
    }


async def verify_d5_regulatory_review_bridge(
    db,
    snapshot,
):
    """Rebuild both sides; fail if either changed or was forged."""

    if type(snapshot) is not dict or set(snapshot) != {
        "body", "bridge_sha256"
    }:
        raise D5RegulatoryBridgeError(
            "Invalid bridge envelope"
        )

    body = snapshot["body"]

    if type(body) is not dict:
        raise D5RegulatoryBridgeError(
            "Invalid bridge body"
        )

    if not isinstance(
        snapshot["bridge_sha256"], str
    ) or _digest(body) != snapshot["bridge_sha256"]:
        raise D5RegulatoryBridgeError(
            "Bridge integrity digest mismatch"
        )

    required = {
        "schema",
        "company_id",
        "payroll_period_id",
        "regulatory_review_sha256",
        "d5_source_snapshot",
        "d5_target_count",
        "review_only",
        "normative_approval",
        "official_generation",
        "official_rows_generated",
    }

    if set(body) != required:
        raise D5RegulatoryBridgeError(
            "Bridge fields do not match schema"
        )

    if (
        body["schema"] != SCHEMA
        or body["review_only"] is not True
        or body["normative_approval"] is not False
        or body["official_generation"] != "BLOCKED"
        or type(body["official_rows_generated"]) is not int
        or body["official_rows_generated"] != 0
    ):
        raise D5RegulatoryBridgeError(
            "Unsupported regulatory bridge state"
        )

    source = body["d5_source_snapshot"]

    if (
        type(source) is not dict
        or type(source.get("body")) is not dict
        or type(source["body"].get("entries")) is not list
    ):
        raise D5RegulatoryBridgeError(
            "Invalid source snapshot"
        )

    try:
        rebuilt = await build_d5_regulatory_review_bridge(
            db,
            company_id=body["company_id"],
            payroll_period_id=body["payroll_period_id"],
            targets=[
                item["target"]
                for item in source["body"]["entries"]
            ],
        )
    except Exception as exc:
        raise D5RegulatoryBridgeError(
            "Live regulatory/source verification failed"
        ) from exc

    if rebuilt != snapshot:
        raise D5RegulatoryBridgeError(
            "Bridge no longer matches PostgreSQL sources"
        )

    return True
