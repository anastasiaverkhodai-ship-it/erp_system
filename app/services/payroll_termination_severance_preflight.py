"""Read-only link from final-payroll input to documented termination."""

from dataclasses import dataclass
from datetime import date

from sqlalchemy import select

from app.models.employment_contract import EmploymentContract
from app.models.employment_event import EmploymentEvent
from app.models.payroll import PayrollInput, PayrollPeriod

from app.services.payroll_termination_severance_policy import (
    SeverancePolicy,
    classify_severance_basis,
)


class SeverancePreflightError(ValueError):
    """The termination source is not suitable for payroll settlement."""


@dataclass(frozen=True)
class SeverancePreflight:
    company_id: int
    payroll_input_id: int
    employment_contract_id: int
    termination_event_id: int
    termination_date: date
    termination_order_number: str
    policy: SeverancePolicy


def _status(value):
    return getattr(value, "value", value)


async def preflight_termination_severance(
    db,
    *,
    company_id: int,
    payroll_input_id: int,
    allow_finalized_source: bool = False,
) -> SeverancePreflight:
    """Inspect persisted evidence; make no financial or database writes."""

    if company_id <= 0 or payroll_input_id <= 0:
        raise SeverancePreflightError("Invalid company or payroll input")

    if allow_finalized_source:
        from app.models.payroll_termination_severance import (
            PayrollTerminationSeverance,
        )

        existing_source_id = await db.scalar(
            select(PayrollTerminationSeverance.id)
            .where(
                PayrollTerminationSeverance.company_id
                == company_id,
                PayrollTerminationSeverance.payroll_input_id
                == payroll_input_id,
            )
            .limit(1)
        )

        if existing_source_id is None:
            raise SeverancePreflightError(
                "Finalized validation requires an existing "
                "immutable severance source"
            )

    payroll_input = await db.scalar(
        select(PayrollInput).where(
            PayrollInput.company_id == company_id,
            PayrollInput.id == payroll_input_id,
        )
    )
    if (
        payroll_input is None
        or payroll_input.company_id != company_id
        or payroll_input.id != payroll_input_id
    ):
        raise SeverancePreflightError(
            "Company payroll input not found"
        )

    period = await db.scalar(
        select(PayrollPeriod).where(
            PayrollPeriod.company_id == company_id,
            PayrollPeriod.id == payroll_input.payroll_period_id,
        )
    )
    if (
        period is None
        or period.company_id != company_id
        or period.id != payroll_input.payroll_period_id
        or (
            _status(period.status) != "draft"
            and not (
                allow_finalized_source
                and _status(period.status) == "finalized"
            )
        )
    ):
        raise SeverancePreflightError(
            "An open company payroll period is required"
        )

    contract = await db.scalar(
        select(EmploymentContract).where(
            EmploymentContract.company_id == company_id,
            EmploymentContract.id
            == payroll_input.employment_contract_id,
        )
    )
    if (
        contract is None
        or contract.company_id != company_id
        or contract.id != payroll_input.employment_contract_id
        or _status(contract.status) != "ended"
        or contract.end_date is None
    ):
        raise SeverancePreflightError(
            "Ended employment contract is required"
        )

    if not (
        period.start_date
        <= contract.end_date
        <= period.end_date
    ):
        raise SeverancePreflightError(
            "Termination must fall within the payroll period"
        )

    event = await db.scalar(
        select(EmploymentEvent).where(
            EmploymentEvent.company_id == company_id,
            EmploymentEvent.employment_contract_id == contract.id,
            EmploymentEvent.event_type == "termination",
            EmploymentEvent.effective_date == contract.end_date,
        ).order_by(EmploymentEvent.id.desc())
    )
    if (
        event is None
        or event.company_id != company_id
        or event.employment_contract_id != contract.id
        or event.event_type != "termination"
        or event.effective_date != contract.end_date
        or not str(event.order_number or "").strip()
    ):
        raise SeverancePreflightError(
            "Active documented termination event is required"
        )

    reversal_id = await db.scalar(
        select(EmploymentEvent.id).where(
            EmploymentEvent.company_id == company_id,
            EmploymentEvent.reversal_of_id == event.id,
        ).limit(1)
    )
    if reversal_id is not None:
        raise SeverancePreflightError(
            "Reversed termination cannot support severance"
        )

    payload = event.request_payload
    if not isinstance(payload, dict):
        raise SeverancePreflightError(
            "Invalid immutable termination event payload"
        )

    try:
        policy = classify_severance_basis(
            payload.get("termination_basis")
        )
    except ValueError as exc:
        raise SeverancePreflightError(
            "Invalid documented termination legal basis"
        ) from exc

    return SeverancePreflight(
        company_id=company_id,
        payroll_input_id=payroll_input_id,
        employment_contract_id=contract.id,
        termination_event_id=event.id,
        termination_date=event.effective_date,
        termination_order_number=event.order_number,
        policy=policy,
    )
