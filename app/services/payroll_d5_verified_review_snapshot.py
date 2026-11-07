"""D5 source-bound, tamper-evident review snapshots.

Not a statutory report, signature, or immutable database record.
"""

from hashlib import sha256
import json

from app.services.payroll_d5_database_field_projection import (
    project_persisted_d5_event,
)
from app.services.payroll_d5_database_category_transition import (
    project_persisted_d5_category_transition,
)
from app.services.payroll_d5_database_correction_chain import (
    review_persisted_d5_correction_chain,
)


class D5ReviewSnapshotError(ValueError):
    pass


SCHEMA = "D5_INTERNAL_REVIEW_V1"

TARGET_KEYS = {
    "event": frozenset((
        "kind", "employment_event_id",
    )),
    "category_transition": frozenset((
        "kind",
        "employment_event_id",
        "previous_evidence_id",
    )),
    "correction_chain": frozenset((
        "kind",
        "original_event_id",
        "reversal_event_id",
        "replacement_event_id",
    )),
}


def _positive(value, name):
    if type(value) is not int or value <= 0:
        raise D5ReviewSnapshotError(
            "Invalid " + name
        )


def _json(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _digest(value):
    return sha256(_json(value)).hexdigest()


def _targets(targets):
    if not isinstance(targets, (tuple, list)):
        raise D5ReviewSnapshotError(
            "Targets must be a sequence"
        )

    if not 1 <= len(targets) <= 100:
        raise D5ReviewSnapshotError(
            "Expected 1 to 100 review targets"
        )

    normalized = []
    seen = set()

    for target in targets:
        if type(target) is not dict:
            raise D5ReviewSnapshotError(
                "Invalid target structure"
            )

        kind = target.get("kind")
        expected = TARGET_KEYS.get(kind)

        if expected is None or set(target) != expected:
            raise D5ReviewSnapshotError(
                "Unsupported or incomplete target"
            )

        for key, value in target.items():
            if key != "kind":
                _positive(value, key)

        canonical = _json(target)

        if canonical in seen:
            raise D5ReviewSnapshotError(
                "Duplicate review target"
            )

        seen.add(canonical)
        normalized.append(dict(target))

    # Stable order, independent of request order.
    normalized.sort(key=_json)
    return normalized


async def _project(db, company_id, target):
    kind = target["kind"]

    if kind == "event":
        result = await project_persisted_d5_event(
            db,
            company_id=company_id,
            employment_event_id=target[
                "employment_event_id"
            ],
        )

        return {
            "kind": kind,
            "source_sha256": result[
                "persisted_erp_evidence_sha256"
            ],
            "candidate_sha256": result[
                "candidate"
            ]["candidate_sha256"],
            "professional_evidence_id": result[
                "professional_evidence_id"
            ],
            "official_generation": result[
                "official_generation"
            ],
        }

    if kind == "category_transition":
        result = await project_persisted_d5_category_transition(
            db,
            company_id=company_id,
            employment_event_id=target[
                "employment_event_id"
            ],
            previous_evidence_id=target[
                "previous_evidence_id"
            ],
        )

        return {
            "kind": kind,
            "previous_evidence_sha256": result[
                "previous_evidence_sha256"
            ],
            "current_evidence_sha256": result[
                "current_evidence_sha256"
            ],
            "previous_event_id": result[
                "previous_event_id"
            ],
            "previous_professional_evidence_id": result[
                "previous_professional_evidence_id"
            ],
            "current_professional_evidence_id": result[
                "current_professional_evidence_id"
            ],
            "official_generation": result[
                "official_generation"
            ],
        }

    result = await review_persisted_d5_correction_chain(
        db,
        company_id=company_id,
        original_event_id=target[
            "original_event_id"
        ],
        reversal_event_id=target[
            "reversal_event_id"
        ],
        replacement_event_id=target[
            "replacement_event_id"
        ],
    )

    return {
        "kind": kind,
        "correction_chain_sha256": result[
            "correction_chain_sha256"
        ],
        "original_source_sha256": result[
            "original_source_sha256"
        ],
        "replacement_source_sha256": result[
            "replacement_source_sha256"
        ],
        "official_generation": result[
            "official_generation"
        ],
    }


async def build_d5_review_snapshot(
    db,
    *,
    company_id,
    targets,
):
    """Re-read persisted sources and record their review fingerprints."""

    _positive(company_id, "company_id")
    normalized = _targets(targets)

    entries = []

    for target in normalized:
        try:
            result = await _project(
                db, company_id, target
            )
        except Exception as exc:
            raise D5ReviewSnapshotError(
                "Persisted D5 review source rejected"
            ) from exc

        if result["official_generation"] != "BLOCKED":
            raise D5ReviewSnapshotError(
                "Unexpected statutory generation state"
            )

        entries.append({
            "target": target,
            "source_review": result,
        })

    body = {
        "schema": SCHEMA,
        "company_id": company_id,
        "entries": entries,
        "entry_count": len(entries),
        "review_only": True,
        "official_generation": "BLOCKED",
        "official_rows_generated": 0,
        "normative_approval": False,
    }

    return {
        "body": body,
        "snapshot_sha256": _digest(body),
    }


async def verify_d5_review_snapshot(
    db,
    snapshot,
):
    """Verify integrity and re-read live PostgreSQL evidence."""

    if type(snapshot) is not dict or set(snapshot) != {
        "body", "snapshot_sha256"
    }:
        raise D5ReviewSnapshotError(
            "Invalid snapshot envelope"
        )

    body = snapshot["body"]
    digest = snapshot["snapshot_sha256"]

    if type(body) is not dict or not isinstance(
        digest, str
    ):
        raise D5ReviewSnapshotError(
            "Invalid snapshot payload"
        )

    if _digest(body) != digest:
        raise D5ReviewSnapshotError(
            "Snapshot content digest mismatch"
        )

    if body.get("schema") != SCHEMA:
        raise D5ReviewSnapshotError(
            "Unsupported snapshot schema"
        )

    if body.get("official_generation") != "BLOCKED":
        raise D5ReviewSnapshotError(
            "Unexpected official generation state"
        )

    if (
        body.get("review_only") is not True
        or body.get("normative_approval") is not False
        or body.get("official_rows_generated") != 0
    ):
        raise D5ReviewSnapshotError(
            "Invalid review-only state"
        )

    entries = body.get("entries")

    if not isinstance(entries, list):
        raise D5ReviewSnapshotError(
            "Missing review entries"
        )

    if body.get("entry_count") != len(entries):
        raise D5ReviewSnapshotError(
            "Review entry count mismatch"
        )

    if any(
        type(item) is not dict
        or set(item) != {"target", "source_review"}
        for item in entries
    ):
        raise D5ReviewSnapshotError(
            "Invalid review entry"
        )

    try:
        rebuilt = await build_d5_review_snapshot(
            db,
            company_id=body["company_id"],
            targets=[
                item["target"]
                for item in entries
            ],
        )
    except Exception as exc:
        raise D5ReviewSnapshotError(
            "Live PostgreSQL source verification failed"
        ) from exc

    if rebuilt != snapshot:
        raise D5ReviewSnapshotError(
            "Snapshot no longer matches PostgreSQL sources"
        )

    return True
