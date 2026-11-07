"""Source-bound D5 intake for persisted HR lifecycle events.

Supported source kinds: hire, transfer, termination.
Reversal is never a new profession-evidence event.

This does not produce or authorize statutory D5 report rows.
Candidate history and independently approved classifier evidence
remain mandatory for any successful persistence.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.employment_event import EmploymentEvent
from app.services.payroll_d5_event_sources import D5EventSource
from app.services.payroll_d5_position_provenance import (
    D5PositionEvidence,
)
from app.services.payroll_d5_professional_history import (
    D5ProfessionalHistoryRecord,
)
from app.services.payroll_d5_profession_history import HistoryResult
from app.services.payroll_d5_verified_mapping import (
    D5MappingError,
    map_verified_d5_candidate,
)
from app.services.payroll_d5_source_history_binding import (
    create_source_bound_d5_professional_evidence,
)


class D5HREventIntakeError(ValueError):
    pass


SUPPORTED_EVENTS = frozenset({
    "hire",
    "transfer",
    "termination",
})


async def persist_verified_d5_hr_event(
    db: AsyncSession,
    *,
    source: D5EventSource,
    evidence: D5PositionEvidence,
    history: D5ProfessionalHistoryRecord,
    candidate_history: HistoryResult,
    classifier_bytes: bytes,
    request_key: str,
    created_by: int,
):
    """Verify a documented live HR event then persist evidence.

    Caller controls the transaction. The existing source-bound
    writer owns profession approval, HR position provenance,
    idempotency and database insert-only enforcement.
    """

    if not isinstance(db, AsyncSession):
        raise D5HREventIntakeError(
            "Async database session required"
        )

    if not isinstance(source, D5EventSource):
        raise D5HREventIntakeError(
            "Typed HR event source required"
        )

    if source.event_type not in SUPPORTED_EVENTS:
        raise D5HREventIntakeError(
            "Unsupported HR event type"
        )

    if not isinstance(evidence, D5PositionEvidence):
        raise D5HREventIntakeError(
            "Typed position evidence required"
        )

    if not isinstance(history, D5ProfessionalHistoryRecord):
        raise D5HREventIntakeError(
            "Typed professional history required"
        )

    if not isinstance(candidate_history, HistoryResult):
        raise D5HREventIntakeError(
            "Typed candidate history required"
        )

    for value in (
        source.company_id,
        source.employment_event_id,
        source.employment_contract_id,
    ):
        if type(value) is not int or value <= 0:
            raise D5HREventIntakeError(
                "Invalid persisted HR event identity"
            )

    # Validate the existing internal mapping, particularly the
    # structured legal basis required for termination.
    # This is not statutory form-field authorization.
    try:
        mapped = map_verified_d5_candidate(
            source,
            evidence.professional,
        )
    except D5MappingError as exc:
        raise D5HREventIntakeError(
            f"Documented HR mapping rejected: {exc}"
        ) from exc

    if (
        mapped.event_kind != source.event_type
        or mapped.source_event_id != source.employment_event_id
        or mapped.contract_id != source.employment_contract_id
    ):
        raise D5HREventIntakeError(
            "HR mapping identity mismatch"
        )

    # Lock the actual source row during validation and write.
    # Do not trust an event identifier alone.
    event = await db.scalar(
        select(EmploymentEvent).where(
            EmploymentEvent.id == source.employment_event_id,
            EmploymentEvent.company_id == source.company_id,
        ).with_for_update()
    )

    if event is None:
        raise D5HREventIntakeError(
            "Persisted HR event not found"
        )

    if event.reversal_of_id is not None:
        raise D5HREventIntakeError(
            "Reversal event cannot create D5 evidence"
        )

    if (
        event.event_type != source.event_type
        or event.employment_contract_id
        != source.employment_contract_id
        or event.effective_date != source.effective_date
        or event.order_number != source.order_number
        or event.order_date != source.order_date
    ):
        raise D5HREventIntakeError(
            "HR event differs from persisted source"
        )

    if source.event_type == "termination":
        if (
            not isinstance(event.termination_basis, dict)
            or event.termination_basis
            != source.termination_basis
        ):
            raise D5HREventIntakeError(
                "Termination legal basis differs from HR source"
            )
    elif source.termination_basis is not None:
        raise D5HREventIntakeError(
            "Nontermination event has termination basis"
        )

    reversed_event = await db.scalar(
        select(EmploymentEvent.id).where(
            EmploymentEvent.company_id == source.company_id,
            EmploymentEvent.reversal_of_id == event.id,
            EmploymentEvent.event_type == "reversal",
        ).limit(1)
    )

    if reversed_event is not None:
        raise D5HREventIntakeError(
            "Reversed HR event cannot create D5 evidence"
        )

    return await create_source_bound_d5_professional_evidence(
        db,
        source=source,
        evidence=evidence,
        history=history,
        candidate_history=candidate_history,
        classifier_bytes=classifier_bytes,
        request_key=request_key,
        created_by=created_by,
    )
