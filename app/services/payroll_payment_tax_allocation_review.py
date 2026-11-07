"""Review and revoke payment-tax allocation candidates.

This workflow never approves real withholding or 4DF.
Candidate accounting history remains immutable.
"""

from sqlalchemy import text

from app.services.payroll_payment_tax_allocation_candidate import (
    PaymentTaxAllocationError,
    require_id,
    require_text,
)


EVENT_TABLE = (
    "payroll_payment_tax_allocation_candidate_events"
)
CANDIDATE_TABLE = (
    "payroll_payment_tax_allocation_candidates"
)


def resolve_event_state(events):
    """Rebuild state from a complete ordered event history."""

    state = "draft"
    seen = set()

    for event in events:
        if event not in ("reviewed", "revoked"):
            raise PaymentTaxAllocationError(
                "Unknown allocation event"
            )

        if event in seen:
            raise PaymentTaxAllocationError(
                "Duplicate allocation event"
            )

        if state == "revoked":
            raise PaymentTaxAllocationError(
                "Event after revocation"
            )

        if event == "reviewed":
            state = "reviewed"
        else:
            state = "revoked"

        seen.add(event)

    return state


async def get_candidate_state(
    db, *, company_id, candidate_id,
):
    company_id = require_id(company_id, "company")
    candidate_id = require_id(candidate_id, "candidate")

    candidate = (
        await db.execute(
            text(f"""
                SELECT id
                FROM {CANDIDATE_TABLE}
                WHERE company_id=:company
                  AND id=:candidate
            """),
            {
                "company": company_id,
                "candidate": candidate_id,
            },
        )
    ).scalar_one_or_none()

    if candidate is None:
        raise PaymentTaxAllocationError(
            "Candidate not found for company"
        )

    events = (
        await db.execute(
            text(f"""
                SELECT event_kind
                FROM {EVENT_TABLE}
                WHERE company_id=:company
                  AND candidate_id=:candidate
                ORDER BY id
            """),
            {
                "company": company_id,
                "candidate": candidate_id,
            },
        )
    ).scalars().all()

    state = resolve_event_state(events)

    return {
        "candidate_id": candidate_id,
        "state": state,
        "event_count": len(events),
        "payment_specific_withholding_verified": False,
        "gross_paid_approved": False,
        "official_generation": "BLOCKED",
    }


async def record_candidate_event(
    db,
    *,
    company_id,
    candidate_id,
    event_kind,
    request_key,
    evidence_reference,
    actor_user_id,
):
    company_id = require_id(company_id, "company")
    candidate_id = require_id(candidate_id, "candidate")
    actor_user_id = require_id(actor_user_id, "actor")

    request_key = require_text(
        request_key, "request key", 150
    )
    evidence_reference = require_text(
        evidence_reference, "evidence reference", 500
    )

    if event_kind not in ("reviewed", "revoked"):
        raise PaymentTaxAllocationError(
            "Unsupported event kind"
        )

    candidate = (
        await db.execute(
            text(f"""
                SELECT id, created_by
                FROM {CANDIDATE_TABLE}
                WHERE company_id=:company
                  AND id=:candidate
                FOR UPDATE
            """),
            {
                "company": company_id,
                "candidate": candidate_id,
            },
        )
    ).mappings().first()

    if candidate is None:
        raise PaymentTaxAllocationError(
            "Candidate not found for company"
        )

    previous_request = (
        await db.execute(
            text(f"""
                SELECT id, candidate_id, event_kind,
                       evidence_reference, actor_user_id
                FROM {EVENT_TABLE}
                WHERE company_id=:company
                  AND request_key=:key
            """),
            {
                "company": company_id,
                "key": request_key,
            },
        )
    ).mappings().first()

    if previous_request is not None:
        if (
            previous_request["candidate_id"] != candidate_id
            or previous_request["event_kind"] != event_kind
            or previous_request["evidence_reference"]
                != evidence_reference
            or previous_request["actor_user_id"]
                != actor_user_id
        ):
            raise PaymentTaxAllocationError(
                "Event idempotency conflict"
            )

        return {
            "event_id": previous_request["id"],
            "idempotent": True,
            **(await get_candidate_state(
                db,
                company_id=company_id,
                candidate_id=candidate_id,
            )),
        }

    current = await get_candidate_state(
        db,
        company_id=company_id,
        candidate_id=candidate_id,
    )

    if current["state"] == "revoked":
        raise PaymentTaxAllocationError(
            "Revoked candidate cannot be reused"
        )

    if (
        event_kind == "reviewed"
        and candidate["created_by"] == actor_user_id
    ):
        raise PaymentTaxAllocationError(
            "Self-review is not permitted"
        )

    if (
        event_kind == "reviewed"
        and current["state"] != "draft"
    ):
        raise PaymentTaxAllocationError(
            "Candidate already reviewed"
        )

    result = await db.execute(
        text(f"""
            INSERT INTO {EVENT_TABLE} (
                company_id, candidate_id,
                event_kind, request_key,
                evidence_reference, actor_user_id
            ) VALUES (
                :company, :candidate,
                :kind, :key,
                :evidence, :actor
            )
            RETURNING id
        """),
        {
            "company": company_id,
            "candidate": candidate_id,
            "kind": event_kind,
            "key": request_key,
            "evidence": evidence_reference,
            "actor": actor_user_id,
        },
    )

    new_state = await get_candidate_state(
        db,
        company_id=company_id,
        candidate_id=candidate_id,
    )

    return {
        "event_id": result.scalar_one(),
        "idempotent": False,
        **new_state,
    }
