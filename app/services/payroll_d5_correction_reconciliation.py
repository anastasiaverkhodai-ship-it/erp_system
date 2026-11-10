"""Read-only D5 correction reconciliation.

The result is an internal candidate decision, never
an approved statutory filing correction or official row.
"""

from hashlib import sha256
import json

from app.services.payroll_d5_correction_chain import (
    review_persisted_d5_correction_chain,
)
from app.services.payroll_d5_database_field_projection import (
    project_persisted_d5_event,
)


class D5CorrectionReconciliationError(ValueError):
    pass


def _positive(value, name):
    if type(value) is not int or value <= 0:
        raise D5CorrectionReconciliationError(
            f"Invalid {name}"
        )


def _digest(payload):
    return sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def reconcile_d5_correction_candidate(
    *,
    company_id,
    employment_event_id,
    chain,
    persisted_projection=None,
):
    """Determine whether an HR event remains a D5 candidate.

    Reversed events are suppressed; a blocked correction chain
    cannot produce a candidate. No statutory rows are generated.
    """
    _positive(company_id, "company_id")
    _positive(employment_event_id, "employment_event_id")

    if type(chain) is not dict:
        raise D5CorrectionReconciliationError(
            "Correction chain required"
        )

    if (
        chain.get("company_id") != company_id
        or chain.get("employment_event_id")
        != employment_event_id
    ):
        raise D5CorrectionReconciliationError(
            "Correction source identity mismatch"
        )

    if chain.get("official_generation") != "BLOCKED":
        raise D5CorrectionReconciliationError(
            "Unsafe correction chain state"
        )

    if chain.get("official_rows_generated") != 0:
        raise D5CorrectionReconciliationError(
            "Official correction rows must be zero"
        )

    chain_hash = chain.get("correction_chain_sha256")

    if (
        type(chain_hash) is not str
        or len(chain_hash) != 64
        or any(c not in "0123456789abcdef" for c in chain_hash)
    ):
        raise D5CorrectionReconciliationError(
            "Verified chain digest required"
        )

    state = chain.get("chain_state")
    blockers = chain.get("blockers")
    ids = chain.get("chain_event_ids")
    reversals = chain.get("reversal_count")

    if (
        type(blockers) is not list
        or not all(type(x) is str for x in blockers)
        or type(ids) is not list
        or not ids
        or ids[0] != employment_event_id
        or any(type(x) is not int or x <= 0 for x in ids)
        or len(set(ids)) != len(ids)
        or type(reversals) is not int
        or reversals != len(ids) - 1
    ):
        raise D5CorrectionReconciliationError(
            "Malformed correction chain"
        )

    if (
        state == "NO_REVERSAL_RECORDED"
        and reversals == 0
        and not blockers
    ):
        decision = "CANDIDATE_RETAINED"

    elif (
        state == "SOURCE_REVERSED"
        and reversals == 1
        and not blockers
    ):
        decision = "CANDIDATE_SUPPRESSED"

    else:
        decision = "REVIEW_BLOCKED"

    projection_hash = None

    if decision == "CANDIDATE_RETAINED":
        if type(persisted_projection) is not dict:
            raise D5CorrectionReconciliationError(
                "Active persisted projection required"
            )

        if persisted_projection.get("source_binding") != (
            "PERSISTED_POSTGRESQL_RECORDS_VERIFIED"
        ):
            raise D5CorrectionReconciliationError(
                "Persisted HR source verification required"
            )

        if (
            persisted_projection.get("official_generation")
            != "BLOCKED"
            or persisted_projection.get("official_rows_generated")
            != 0
        ):
            raise D5CorrectionReconciliationError(
                "Unsafe persisted projection state"
            )

        identity = persisted_projection.get(
            "candidate", {}
        ).get("identity", {})

        if (
            identity.get("company_id") != company_id
            or identity.get("employment_event_id")
            != employment_event_id
        ):
            raise D5CorrectionReconciliationError(
                "Persisted candidate identity mismatch"
            )

        projection_hash = persisted_projection.get(
            "persisted_erp_evidence_sha256"
        )

        if (
            type(projection_hash) is not str
            or len(projection_hash) != 64
            or any(
                c not in "0123456789abcdef"
                for c in projection_hash
            )
        ):
            raise D5CorrectionReconciliationError(
                "Persisted HR evidence digest required"
            )

    elif persisted_projection is not None:
        raise D5CorrectionReconciliationError(
            "Suppressed or blocked event cannot retain projection"
        )

    payload = {
        "company_id": company_id,
        "employment_event_id": employment_event_id,
        "chain_sha256": chain_hash,
        "chain_event_ids": ids,
        "chain_state": state,
        "chain_blockers": blockers,
        "decision": decision,
        "persisted_erp_evidence_sha256": projection_hash,
    }

    return {
        **payload,
        "reconciliation_sha256": _digest(payload),
        "candidate_rows_retained": (
            1 if decision == "CANDIDATE_RETAINED" else 0
        ),
        "candidate_rows_suppressed": (
            1 if decision == "CANDIDATE_SUPPRESSED" else 0
        ),
        "full_filing_correction_history": "NOT_VERIFIED",
        "normative_mapping": "NOT_APPROVED",
        "official_generation": "BLOCKED",
        "official_rows_generated": 0,
    }


async def review_persisted_d5_correction_reconciliation(
    db,
    *,
    company_id,
    employment_event_id,
):
    """Reconcile the actual PostgreSQL reversal chain to D5.

    The existing active-event projection is called only for
    an event without recorded reversals.
    """
    _positive(company_id, "company_id")
    _positive(employment_event_id, "employment_event_id")

    chain = await review_persisted_d5_correction_chain(
        db,
        company_id=company_id,
        employment_event_id=employment_event_id,
    )

    projection = None

    if chain["chain_state"] == "NO_REVERSAL_RECORDED":
        projection = await project_persisted_d5_event(
            db,
            company_id=company_id,
            employment_event_id=employment_event_id,
        )

    return reconcile_d5_correction_candidate(
        company_id=company_id,
        employment_event_id=employment_event_id,
        chain=chain,
        persisted_projection=projection,
    )
