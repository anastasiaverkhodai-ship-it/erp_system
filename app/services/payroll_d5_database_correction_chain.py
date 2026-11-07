"""Review-only D5 HR-source correction-chain verification.

This is not a statutory D5 correcting-report generator.
"""

from sqlalchemy import select

from app.models.employment_event import EmploymentEvent
from app.models.payroll_d5_professional_evidence import (
    PayrollD5ProfessionalEvidence,
)
from app.services.payroll_d5_database_field_projection import (
    _canonical_digest,
    project_persisted_d5_event,
)


class D5CorrectionChainError(ValueError):
    pass


SUPPORTED = frozenset({"hire", "transfer"})


def _id(value, name):
    if type(value) is not int or value <= 0:
        raise D5CorrectionChainError("Invalid " + name)


def _validate_link(original, reversal, replacement):
    """Fail closed on ambiguous or forged replacement links."""

    if original.event_type not in SUPPORTED:
        raise D5CorrectionChainError(
            "Event kind requires separate correction review"
        )

    if (
        reversal.event_type != "reversal"
        or reversal.reversal_of_id != original.id
    ):
        raise D5CorrectionChainError(
            "Invalid original-event reversal"
        )

    if (
        replacement.event_type != original.event_type
        or replacement.id in (original.id, reversal.id)
    ):
        raise D5CorrectionChainError(
            "Invalid replacement event type or identity"
        )

    if len({
        original.company_id,
        reversal.company_id,
        replacement.company_id,
    }) != 1:
        raise D5CorrectionChainError(
            "Cross-company correction rejected"
        )

    if len({
        original.employment_contract_id,
        reversal.employment_contract_id,
        replacement.employment_contract_id,
    }) != 1:
        raise D5CorrectionChainError(
            "Cross-contract correction rejected"
        )

    if replacement.effective_date != original.effective_date:
        raise D5CorrectionChainError(
            "Different-period correction requires separate review"
        )

    payload = replacement.request_payload

    if (
        not isinstance(payload, dict)
        or type(payload.get("replaces_employment_event_id")) is not int
        or payload["replaces_employment_event_id"] != original.id
    ):
        raise D5CorrectionChainError(
            "Explicit persisted replacement link required"
        )

    if (
        not isinstance(replacement.order_number, str)
        or not replacement.order_number.strip()
        or replacement.order_number == original.order_number
    ):
        raise D5CorrectionChainError(
            "Distinct documented replacement order required"
        )



def _require_unique_replacement(
    candidates,
    *,
    original_event_id,
    expected_replacement_id,
):
    """One explicit replacement per original HR event."""

    links = []

    for event in candidates:
        payload = event.request_payload

        if not isinstance(payload, dict):
            continue

        link = payload.get("replaces_employment_event_id")

        if type(link) is int and link == original_event_id:
            links.append(event.id)

    if links != [expected_replacement_id]:
        raise D5CorrectionChainError(
            "Unique active replacement link required"
        )

    return True


async def review_persisted_d5_correction_chain(
    db,
    *,
    company_id,
    original_event_id,
    reversal_event_id,
    replacement_event_id,
):
    """Review a source reversal and explicitly linked replacement."""

    for value, name in (
        (company_id, "company_id"),
        (original_event_id, "original_event_id"),
        (reversal_event_id, "reversal_event_id"),
        (replacement_event_id, "replacement_event_id"),
    ):
        _id(value, name)

    if len({
        original_event_id,
        reversal_event_id,
        replacement_event_id,
    }) != 3:
        raise D5CorrectionChainError(
            "Correction requires three distinct events"
        )

    async def get_event(event_id):
        return await db.scalar(
            select(EmploymentEvent).where(
                EmploymentEvent.id == event_id,
                EmploymentEvent.company_id == company_id,
            )
        )

    original = await get_event(original_event_id)
    reversal = await get_event(reversal_event_id)
    replacement = await get_event(replacement_event_id)

    if None in (original, reversal, replacement):
        raise D5CorrectionChainError(
            "Correction event absent from company scope"
        )

    _validate_link(original, reversal, replacement)

    # A replacement is not unique merely because the caller
    # supplies one event ID. Inspect all matching persisted
    # HR events in the company/contract/date scope.
    replacement_candidates = (
        await db.scalars(
            select(EmploymentEvent).where(
                EmploymentEvent.company_id == company_id,
                EmploymentEvent.employment_contract_id
                == original.employment_contract_id,
                EmploymentEvent.event_type == original.event_type,
                EmploymentEvent.effective_date
                == original.effective_date,
            ).order_by(EmploymentEvent.id)
        )
    ).all()

    _require_unique_replacement(
        replacement_candidates,
        original_event_id=original.id,
        expected_replacement_id=replacement.id,
    )

    reversal_ids = (
        await db.scalars(
            select(EmploymentEvent.id).where(
                EmploymentEvent.company_id == company_id,
                EmploymentEvent.event_type == "reversal",
                EmploymentEvent.reversal_of_id
                == original_event_id,
            )
        )
    ).all()

    if len(reversal_ids) != 1 or reversal_ids[0] != reversal.id:
        raise D5CorrectionChainError(
            "Unique persisted reversal required"
        )

    replacement_reversal = await db.scalar(
        select(EmploymentEvent.id).where(
            EmploymentEvent.company_id == company_id,
            EmploymentEvent.event_type == "reversal",
            EmploymentEvent.reversal_of_id
            == replacement_event_id,
        )
    )

    if replacement_reversal is not None:
        raise D5CorrectionChainError(
            "Replacement event was reversed"
        )

    # Validate the original source's professional history without
    # presenting the reversed original as an active candidate.
    original_evidence = (
        await db.scalars(
            select(PayrollD5ProfessionalEvidence).where(
                PayrollD5ProfessionalEvidence.company_id
                == company_id,
                PayrollD5ProfessionalEvidence.employment_contract_id
                == original.employment_contract_id,
                PayrollD5ProfessionalEvidence.employment_event_id
                == original_event_id,
            )
        )
    ).all()

    if len(original_evidence) != 1:
        raise D5CorrectionChainError(
            "Unique original professional evidence required"
        )

    old_evidence = original_evidence[0]

    if (
        old_evidence.effective_from > original.effective_date
        or (
            old_evidence.effective_to is not None
            and old_evidence.effective_to < original.effective_date
        )
        or old_evidence.documented_on > original.effective_date
    ):
        raise D5CorrectionChainError(
            "Original professional evidence date mismatch"
        )

    try:
        current = await project_persisted_d5_event(
            db,
            company_id=company_id,
            employment_event_id=replacement_event_id,
        )
    except Exception as exc:
        # This is a review boundary: no unverified replacement
        # is allowed through. Preserve the underlying traceback.
        raise D5CorrectionChainError(
            "Replacement database projection rejected"
        ) from exc

    # The initial and replacement evidence must describe
    # the same persisted employee, not merely the same
    # employment contract identifier supplied by a caller.
    replacement_employee_id = current[
        "candidate"
    ]["identity"]["employee_id"]

    if old_evidence.employee_id != replacement_employee_id:
        raise D5CorrectionChainError(
            "Original/replacement employee identity mismatch"
        )

    old_digest = _canonical_digest({
        "company_id": company_id,
        "original_event_id": original.id,
        "original_kind": original.event_type,
        "original_date": original.effective_date,
        "original_order": original.order_number,
        "original_before": original.before_state,
        "original_after": original.after_state,
        "original_evidence_id": old_evidence.id,
        "original_employee_id": old_evidence.employee_id,
        "original_profession_code": old_evidence.profession_code,
        "original_position_id": old_evidence.position_id,
        "original_evidence_reference":
            old_evidence.evidence_reference,
    })

    chain_digest = _canonical_digest({
        "original_source_sha256": old_digest,
        "reversal_id": reversal.id,
        "reversal_order": reversal.order_number,
        "reversal_payload": reversal.request_payload,
        "replacement_id": replacement.id,
        "replacement_order": replacement.order_number,
        "replacement_payload": replacement.request_payload,
        "replacement_source_sha256":
            current["persisted_erp_evidence_sha256"],
    })

    return {
        "company_id": company_id,
        "original_event_id": original.id,
        "reversal_event_id": reversal.id,
        "replacement_event_id": replacement.id,
        "original_professional_evidence_id": old_evidence.id,
        "replacement_professional_evidence_id":
            current["professional_evidence_id"],
        "original_source_sha256": old_digest,
        "replacement_source_sha256":
            current["persisted_erp_evidence_sha256"],
        "correction_chain_sha256": chain_digest,
        "replacement_candidate": current["candidate"],
        "original_event_status": "REVERSED",
        "replacement_event_status": "ACTIVE_CANDIDATE",
        "correction_type": "INTERNAL_SOURCE_REPLACEMENT_REVIEW",
        "statutory_correction_type_approved": False,
        "normative_mapping_approved": False,
        "official_rows_generated": 0,
        "official_generation": "BLOCKED",
        "review_only": True,
    }
