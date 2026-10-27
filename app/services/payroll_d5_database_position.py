
"""Database verification of historical D5 position provenance.

This service never manufactures Ukrainian profession classifier data.
"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.employee import Employee
from app.models.employment_contract import EmploymentContract
from app.models.employment_event import EmploymentEvent
from app.models.position import Position
from app.services.payroll_d5_event_sources import D5EventSource
from app.services.payroll_d5_position_provenance import (
    D5PositionEvidence,
    D5PositionProvenanceError,
    verify_d5_position_provenance,
)


async def verify_database_d5_position(
    db: AsyncSession,
    *,
    source: D5EventSource,
    evidence: D5PositionEvidence,
):
    """Verify real company-scoped identities and immutable event state."""
    if not isinstance(source, D5EventSource):
        raise D5PositionProvenanceError("Typed event required")
    if not isinstance(evidence, D5PositionEvidence):
        raise D5PositionProvenanceError("Typed evidence required")

    identifiers = (
        source.company_id,
        source.employment_event_id,
        source.employment_contract_id,
        evidence.company_id,
        evidence.employee_id,
        evidence.employment_contract_id,
        evidence.position_id,
        evidence.event_id,
    )
    if any(type(value) is not int or value <= 0 for value in identifiers):
        raise D5PositionProvenanceError("Positive identifiers required")

    if (
        source.company_id != evidence.company_id
        or source.employment_event_id != evidence.event_id
        or source.employment_contract_id != evidence.employment_contract_id
    ):
        raise D5PositionProvenanceError("Evidence scope mismatch")

    event = await db.scalar(
        select(EmploymentEvent).where(
            EmploymentEvent.company_id == source.company_id,
            EmploymentEvent.id == source.employment_event_id,
            EmploymentEvent.employment_contract_id
            == source.employment_contract_id,
        )
    )
    if event is None:
        raise D5PositionProvenanceError("Real HR event not found")

    if (
        event.event_type != source.event_type
        or event.effective_date != source.effective_date
        or event.order_number != source.order_number
        or event.order_date != source.order_date
        or event.termination_basis != source.termination_basis
    ):
        raise D5PositionProvenanceError("HR source snapshot mismatch")

    reversal = await db.scalar(
        select(EmploymentEvent.id).where(
            EmploymentEvent.company_id == source.company_id,
            EmploymentEvent.reversal_of_id == event.id,
            EmploymentEvent.event_type == "reversal",
        )
    )
    if reversal is not None:
        raise D5PositionProvenanceError("HR event was reversed")

    contract = await db.scalar(
        select(EmploymentContract).where(
            EmploymentContract.company_id == source.company_id,
            EmploymentContract.id == source.employment_contract_id,
            EmploymentContract.employee_id == evidence.employee_id,
        )
    )
    if contract is None:
        raise D5PositionProvenanceError("Contract identity mismatch")

    employee = await db.scalar(
        select(Employee).where(
            Employee.company_id == source.company_id,
            Employee.id == evidence.employee_id,
        )
    )
    if employee is None:
        raise D5PositionProvenanceError("Employee identity mismatch")

    position = await db.scalar(
        select(Position).where(
            Position.company_id == source.company_id,
            Position.id == evidence.position_id,
        )
    )
    if position is None:
        raise D5PositionProvenanceError("Historical position not found")

    # Do not use contract.position_id as historical proof.
    # It may have changed since the HR event.
    return verify_d5_position_provenance(
        source=source,
        position=evidence,
        before_state=event.before_state,
        after_state=event.after_state,
    )
