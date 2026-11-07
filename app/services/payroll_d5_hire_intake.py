"""D5 employment hire evidence intake from a persisted HR event.

This service verifies an existing EmploymentEvent before allowing
the previously source-bound, insert-only evidence writer to run.

It does not approve classifier sources, prepare official D5 rows,
submit a statutory report, or replace employment-event workflows.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.employment_event import EmploymentEvent

from app.services.payroll_d5_event_sources import (
    D5EventSource,
)
from app.services.payroll_d5_position_provenance import (
    D5PositionEvidence,
)
from app.services.payroll_d5_professional_history import (
    D5ProfessionalHistoryRecord,
)
from app.services.payroll_d5_profession_history import (
    HistoryResult,
)
from app.services.payroll_d5_hr_event_intake import (
    persist_verified_d5_hr_event,
)


class D5HireIntakeError(ValueError):
    pass


async def persist_verified_d5_hire_event(
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
    """Persist D5 evidence for a real, active HR hire event only.

    Transaction and commit belong to the caller. The underlying writer
    remains responsible for classifier, identity, locking, and
    immutable evidence guarantees.
    """

    if not isinstance(db, AsyncSession):
        raise D5HireIntakeError("Async database session required")

    if not isinstance(source, D5EventSource):
        raise D5HireIntakeError("Typed employment source required")

    if not isinstance(evidence, D5PositionEvidence):
        raise D5HireIntakeError("Typed position evidence required")

    if not isinstance(history, D5ProfessionalHistoryRecord):
        raise D5HireIntakeError("Typed professional history required")

    if not isinstance(candidate_history, HistoryResult):
        raise D5HireIntakeError("Typed candidate history required")

    if (
        type(source.company_id) is not int
        or source.company_id <= 0
        or type(source.employment_event_id) is not int
        or source.employment_event_id <= 0
        or type(source.employment_contract_id) is not int
        or source.employment_contract_id <= 0
    ):
        raise D5HireIntakeError("Invalid HR source identity")

    if source.event_type != "hire":
        raise D5HireIntakeError(
            "This intake accepts hire events only"
        )

    event = await db.scalar(
        select(EmploymentEvent).where(
            EmploymentEvent.id == source.employment_event_id,
            EmploymentEvent.company_id == source.company_id,
        )
    )

    if event is None:
        raise D5HireIntakeError(
            "Persisted HR event not found"
        )

    if (
        event.event_type != "hire"
        or event.employment_contract_id
        != source.employment_contract_id
        or event.effective_date != source.effective_date
        or event.order_number != source.order_number
        or event.order_date != source.order_date
    ):
        raise D5HireIntakeError(
            "HR event differs from persisted source"
        )

    reversed_event_id = await db.scalar(
        select(EmploymentEvent.id).where(
            EmploymentEvent.company_id == source.company_id,
            EmploymentEvent.reversal_of_id == event.id,
        ).limit(1)
    )

    if reversed_event_id is not None:
        raise D5HireIntakeError(
            "Reversed HR event cannot enter D5 evidence"
        )

    # Never persist data through this service without the
    # independently verified, effective-dated classifier path.
    return await persist_verified_d5_hr_event(
        db,
        source=source,
        evidence=evidence,
        history=history,
        candidate_history=candidate_history,
        classifier_bytes=classifier_bytes,
        request_key=request_key,
        created_by=created_by,
    )
