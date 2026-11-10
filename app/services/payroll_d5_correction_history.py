"""Read-only persisted D5 HR event reversal history.

This reviews directly linked reversals. It does not claim
that every possible correction or filing amendment has
been independently verified.
"""

from hashlib import sha256
import json

from sqlalchemy import select

from app.models.employment_event import EmploymentEvent


class D5CorrectionHistoryError(ValueError):
    pass


def _positive(value, name):
    if type(value) is not int or value <= 0:
        raise D5CorrectionHistoryError(
            f"Invalid {name}"
        )


async def review_persisted_d5_correction_history(
    db,
    *,
    company_id,
    employment_event_id,
):
    _positive(company_id, "company_id")
    _positive(employment_event_id, "employment_event_id")

    source = await db.scalar(
        select(EmploymentEvent).where(
            EmploymentEvent.company_id == company_id,
            EmploymentEvent.id == employment_event_id,
        )
    )

    if source is None:
        raise D5CorrectionHistoryError(
            "Persisted HR source not found"
        )

    if source.event_type not in (
        "hire", "transfer", "termination"
    ):
        raise D5CorrectionHistoryError(
            "Unsupported HR source event"
        )

    reversals = (
        await db.scalars(
            select(EmploymentEvent).where(
                EmploymentEvent.company_id == company_id,
                EmploymentEvent.employment_contract_id
                == source.employment_contract_id,
                EmploymentEvent.reversal_of_id == source.id,
            )
        )
    ).all()

    if any(
        row.event_type != "reversal"
        for row in reversals
    ):
        raise D5CorrectionHistoryError(
            "Invalid reversal event type"
        )

    if len(reversals) > 1:
        raise D5CorrectionHistoryError(
            "Ambiguous multiple reversals"
        )

    if any(
        row.id == source.id
        for row in reversals
    ):
        raise D5CorrectionHistoryError(
            "Self-referential reversal"
        )

    ordered = sorted(
        reversals,
        key=lambda row: row.id,
    )

    payload = {
        "company_id": company_id,
        "source_event_id": source.id,
        "employment_contract_id": (
            source.employment_contract_id
        ),
        "source_event_type": source.event_type,
        "source_effective_date": (
            source.effective_date.isoformat()
        ),
        "source_order_number": source.order_number,
        "reversals": [
            {
                "id": row.id,
                "event_type": row.event_type,
                "effective_date": (
                    row.effective_date.isoformat()
                ),
                "order_number": row.order_number,
                "reversal_of_id": row.reversal_of_id,
            }
            for row in ordered
        ],
    }

    digest = sha256(json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")).hexdigest()

    return {
        "company_id": company_id,
        "employment_event_id": source.id,
        "reversal_count": len(reversals),
        "event_review_state": (
            "REVERSAL_RECORDED"
            if reversals else "NO_REVERSAL_RECORDED"
        ),
        "persisted_correction_evidence_sha256": digest,
        "direct_reversal_links": [
            row.id for row in ordered
        ],
        "full_correction_chain": "NOT_VERIFIED",
        "filing_correction_mapping": "NOT_APPROVED",
        "official_generation": "BLOCKED",
        "official_rows_generated": 0,
    }
