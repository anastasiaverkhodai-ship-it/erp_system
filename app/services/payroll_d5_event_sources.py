"""Internal dated HR event sources for future D5 mapping.

Not a statutory D5 row generator. Professional details, applicability,
and official form mapping must be independently validated before any
regulatory report can be marked generated.
"""

from dataclasses import dataclass
from datetime import date

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.employment_event import EmploymentEvent


@dataclass(frozen=True)
class D5EventSource:
    company_id: int
    employment_event_id: int
    employment_contract_id: int
    event_type: str
    effective_date: date
    order_number: str
    order_date: date
    termination_basis: dict | None


class D5EventSourceError(ValueError):
    pass


async def collect_d5_event_sources(
    db: AsyncSession,
    *,
    company_id: int,
    date_from: date,
    date_to: date,
) -> tuple[D5EventSource, ...]:
    """Read effective HR events; never present them as statutory D5 rows."""

    if type(company_id) is not int or company_id <= 0:
        raise D5EventSourceError("Valid company required")

    if (
        not isinstance(date_from, date)
        or isinstance(date_from, __import__("datetime").datetime)
        or not isinstance(date_to, date)
        or isinstance(date_to, __import__("datetime").datetime)
        or date_from > date_to
    ):
        raise D5EventSourceError("Valid ordered date range required")

    reversed_event = EmploymentEvent.__table__.alias("reversed_event")

    reversal_exists = exists(
        select(1).select_from(reversed_event).where(
            reversed_event.c.company_id == EmploymentEvent.company_id,
            reversed_event.c.reversal_of_id == EmploymentEvent.id,
            reversed_event.c.event_type == "reversal",
        )
    )

    statement = (
        select(EmploymentEvent)
        .where(
            EmploymentEvent.company_id == company_id,
            EmploymentEvent.event_type.in_(
                ("hire", "transfer", "termination")
            ),
            EmploymentEvent.effective_date >= date_from,
            EmploymentEvent.effective_date <= date_to,
            ~reversal_exists,
        )
        .order_by(
            EmploymentEvent.effective_date,
            EmploymentEvent.id,
        )
    )

    events = (await db.scalars(statement)).all()

    result = []
    for event in events:
        basis = event.termination_basis
        if event.event_type == "termination":
            if not isinstance(basis, dict):
                raise D5EventSourceError(
                    "Termination lacks structured legal evidence"
                )

            from app.schemas.employment_termination_basis import (
                EmploymentTerminationBasis,
            )

            try:
                EmploymentTerminationBasis.model_validate(basis)
            except Exception as exc:
                raise D5EventSourceError(
                    "Invalid termination legal evidence"
                ) from exc

        if not event.order_number or not event.order_number.strip():
            raise D5EventSourceError("Event lacks documented order")

        result.append(
            D5EventSource(
                company_id=event.company_id,
                employment_event_id=event.id,
                employment_contract_id=event.employment_contract_id,
                event_type=event.event_type,
                effective_date=event.effective_date,
                order_number=event.order_number,
                order_date=event.order_date,
                termination_basis=dict(basis) if basis else None,
            )
        )

    return tuple(result)
