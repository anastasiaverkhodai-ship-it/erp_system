"""Read-only source-bound D5 HR reversal-chain assessment.

This module verifies persisted reversal relationships.
It does not assert that a reversal of a reversal legally
reinstates the original event or approves D5 corrections.
"""

from hashlib import sha256
import json

from sqlalchemy import select

from app.models.employment_event import EmploymentEvent


class D5CorrectionChainError(ValueError):
    pass


MAX_DEPTH = 16
SOURCE_TYPES = {"hire", "transfer", "termination"}


def _positive(value, name):
    if type(value) is not int or value <= 0:
        raise D5CorrectionChainError(
            f"Invalid {name}"
        )


def _snapshot(row):
    return {
        "id": row.id,
        "company_id": row.company_id,
        "contract_id": row.employment_contract_id,
        "event_type": row.event_type,
        "effective_date": row.effective_date.isoformat(),
        "order_number": row.order_number,
        "reversal_of_id": row.reversal_of_id,
    }


def assess_d5_reversal_chain(
    *,
    company_id,
    employment_event_id,
    snapshots,
    extra_blockers=(),
):
    """Assess a linear, source-bound chain of persisted snapshots.

    All supplied snapshots must form a contiguous chain.
    Branching and malformed relationships remain blocked.
    """
    _positive(company_id, "company_id")
    _positive(employment_event_id, "employment_event_id")

    if type(snapshots) not in (list, tuple):
        raise D5CorrectionChainError(
            "Snapshot collection required"
        )

    if not 1 <= len(snapshots) <= MAX_DEPTH + 1:
        raise D5CorrectionChainError(
            "Invalid correction chain length"
        )

    first = snapshots[0]

    if type(first) is not dict:
        raise D5CorrectionChainError(
            "Invalid source snapshot"
        )

    if first.get("id") != employment_event_id:
        raise D5CorrectionChainError(
            "Source event identity mismatch"
        )

    if first.get("event_type") not in SOURCE_TYPES:
        raise D5CorrectionChainError(
            "Unsupported source event"
        )

    if first.get("reversal_of_id") is not None:
        raise D5CorrectionChainError(
            "Source must not itself be a reversal"
        )

    blockers = set(extra_blockers)
    seen = set()

    for index, item in enumerate(snapshots):
        if type(item) is not dict:
            raise D5CorrectionChainError(
                "Invalid correction snapshot"
            )

        event_id = item.get("id")

        if type(event_id) is not int or event_id <= 0:
            raise D5CorrectionChainError(
                "Invalid event identity"
            )

        if event_id in seen:
            blockers.add("CYCLIC_CORRECTION_CHAIN")

        seen.add(event_id)

        if item.get("company_id") != company_id:
            blockers.add("CROSS_COMPANY_CORRECTION")

        if item.get("contract_id") != first.get("contract_id"):
            blockers.add("CROSS_CONTRACT_CORRECTION")

        if (
            type(item.get("effective_date")) is not str
            or not item["effective_date"]
        ):
            blockers.add("INVALID_EVENT_DATE")

        if (
            type(item.get("order_number")) is not str
            or not item["order_number"].strip()
        ):
            blockers.add("MISSING_CORRECTION_ORDER")

        if index == 0:
            continue

        previous = snapshots[index - 1]

        if item.get("event_type") != "reversal":
            blockers.add("INVALID_CORRECTION_TYPE")

        if item.get("reversal_of_id") != previous["id"]:
            blockers.add("BROKEN_CORRECTION_LINK")

    if len(snapshots) > 2:
        blockers.add("REVERSAL_OF_REVERSAL_REQUIRES_REVIEW")

    if not blockers:
        state = (
            "SOURCE_REVERSED"
            if len(snapshots) == 2
            else "NO_REVERSAL_RECORDED"
        )
    else:
        state = "REVIEW_BLOCKED"

    payload = {
        "company_id": company_id,
        "employment_event_id": employment_event_id,
        "snapshots": snapshots,
        "blockers": sorted(blockers),
    }

    digest = sha256(
        json.dumps(
            payload,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()

    return {
        "company_id": company_id,
        "employment_event_id": employment_event_id,
        "chain_event_ids": [
            item["id"] for item in snapshots
        ],
        "reversal_count": len(snapshots) - 1,
        "chain_state": state,
        "blockers": sorted(blockers),
        "correction_chain_sha256": digest,
        "persisted_relationships": "REVIEWED",
        "complete_filing_correction_history": "NOT_VERIFIED",
        "normative_mapping": "NOT_APPROVED",
        "official_generation": "BLOCKED",
        "official_rows_generated": 0,
    }


async def review_persisted_d5_correction_chain(
    db,
    *,
    company_id,
    employment_event_id,
):
    """Walk directly linked reversals in PostgreSQL.

    The requested company bounds every read. Unexpected
    branches and excessive depth fail closed.
    """
    _positive(company_id, "company_id")
    _positive(employment_event_id, "employment_event_id")

    source = await db.scalar(
        select(EmploymentEvent).where(
            EmploymentEvent.company_id == company_id,
            EmploymentEvent.id == employment_event_id,
        )
    )

    if source is None:
        raise D5CorrectionChainError(
            "Persisted HR source not found"
        )

    if source.event_type not in SOURCE_TYPES:
        raise D5CorrectionChainError(
            "Unsupported original HR source"
        )

    if source.reversal_of_id is not None:
        raise D5CorrectionChainError(
            "Original HR source required"
        )

    snapshots = [_snapshot(source)]
    visited = {source.id}
    current = source
    blockers = set()

    for _ in range(MAX_DEPTH):
        children = (
            await db.scalars(
                select(EmploymentEvent).where(
                    EmploymentEvent.company_id == company_id,
                    EmploymentEvent.reversal_of_id == current.id,
                )
            )
        ).all()

        if not children:
            break

        if len(children) != 1:
            blockers.add("AMBIGUOUS_CORRECTION_BRANCH")
            break

        child = children[0]

        if child.id in visited:
            blockers.add("CYCLIC_CORRECTION_CHAIN")
            break

        visited.add(child.id)
        snapshots.append(_snapshot(child))
        current = child

    else:
        # Reaching the maximum depth must not silently
        # establish completeness.
        blockers.add("CORRECTION_DEPTH_LIMIT")

    return assess_d5_reversal_chain(
        company_id=company_id,
        employment_event_id=employment_event_id,
        snapshots=snapshots,
        extra_blockers=blockers,
    )
