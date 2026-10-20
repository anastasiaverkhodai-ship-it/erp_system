"""Documented severance lines derived from immutable sources."""

from decimal import Decimal
from sqlalchemy import select

from app.models.employment_event import EmploymentEvent
from app.models.payroll import PayrollCalculationLine
from app.models.payroll_termination_severance import (
    PayrollTerminationSeverance,
)
from app.schemas.employment_termination_basis import (
    EmploymentTerminationBasis,
)
from app.services.payroll_termination_severance_policy import (
    calculate_statutory_severance_minimum,
)
from app.services.payroll_termination_severance_preflight import (
    SeverancePreflightError,
    preflight_termination_severance,
)


class SeveranceLineError(ValueError):
    pass


def require_revision_chain(rows):
    """Reject missing, branching or inconsistent source revisions."""
    previous = None

    for expected, row in enumerate(rows, 1):
        if row.revision != expected:
            raise SeveranceLineError(
                "Severance revision sequence is incomplete"
            )

        if row.supersedes_id != (
            previous.id if previous is not None else None
        ):
            raise SeveranceLineError(
                "Severance correction chain is inconsistent"
            )

        previous = row


def build_severance_line(
    *,
    source,
    preflight,
    termination_basis,
    payroll_input,
    period,
):
    """Validate latest source before converting it to a payroll line."""

    policy = preflight.policy

    if policy.category == "manual_review":
        raise SeveranceLineError(
            "Documented severance entitlement is unresolved"
        )

    if (
        source.company_id != preflight.company_id
        or source.payroll_input_id != payroll_input.id
        or source.employment_contract_id
        != payroll_input.employment_contract_id
        or source.termination_event_id
        != preflight.termination_event_id
    ):
        raise SeveranceLineError(
            "Severance source does not belong to this payroll input"
        )

    if (
        source.termination_date != preflight.termination_date
        or source.order_number != preflight.termination_order_number
        or not period.start_date
        <= source.termination_date <= period.end_date
    ):
        raise SeveranceLineError(
            "Severance termination date or order mismatch"
        )

    if (
        source.policy_ground != termination_basis.ground
        or source.labour_code_provision
        != termination_basis.labour_code_provision
        or source.source_reference
        != termination_basis.source_reference
        or source.policy_category != policy.category
        or source.multiplier != policy.multiplier
    ):
        raise SeveranceLineError(
            "Severance policy does not match immutable termination evidence"
        )

    if (
        source.currency_code != "UAH"
        or source.basis_as_of > source.termination_date
        or not str(source.basis_reference or "").strip()
    ):
        raise SeveranceLineError(
            "Invalid dated severance calculation basis"
        )

    base = Decimal(source.base_amount)
    gross = Decimal(source.gross_amount)

    if (
        not base.is_finite()
        or not gross.is_finite()
        or base <= 0
        or gross <= 0
    ):
        raise SeveranceLineError(
            "Invalid severance calculation amount"
        )

    minimum = calculate_statutory_severance_minimum(
        policy,
        average_monthly_earnings=(
            base if policy.category == "average_months" else None
        ),
        effective_minimum_wage=(
            base if policy.category == "minimum_wages" else None
        ),
    )

    # A higher contractual payment needs its own confirmed
    # calculation basis and cannot silently reuse this minimum.
    if gross != minimum:
        raise SeveranceLineError(
            "Severance amount differs from its documented statutory basis"
        )

    return {
        "line_type": "severance",
        "description": (
            f"Severance {source.labour_code_provision}; "
            f"order {source.order_number}"
        ),
        "quantity": Decimal("1.0000"),
        "rate": gross,
        "amount": gross,
        "currency_code": "UAH",
        "source_termination_severance_id": source.id,
    }


async def derive_severance_payroll_lines(
    db,
    *,
    company_id,
    payroll_input,
    period,
):
    rows = list((
        await db.scalars(
            select(PayrollTerminationSeverance)
            .where(
                PayrollTerminationSeverance.company_id == company_id,
                PayrollTerminationSeverance.payroll_input_id
                == payroll_input.id,
            )
            .order_by(
                PayrollTerminationSeverance.revision,
                PayrollTerminationSeverance.id,
            )
        )
    ).all())

    if not rows:
        return []

    require_revision_chain(rows)
    source = rows[-1]

    try:
        preflight = await preflight_termination_severance(
            db,
            company_id=company_id,
            payroll_input_id=payroll_input.id,
            allow_finalized_source=True,
        )
    except SeverancePreflightError as exc:
        raise SeveranceLineError(str(exc)) from exc

    event = await db.scalar(
        select(EmploymentEvent).where(
            EmploymentEvent.company_id == company_id,
            EmploymentEvent.id == preflight.termination_event_id,
        )
    )

    if event is None:
        raise SeveranceLineError(
            "Immutable termination evidence not found"
        )

    basis = EmploymentTerminationBasis.model_validate(
        event.termination_basis
    )

    return [
        build_severance_line(
            source=source,
            preflight=preflight,
            termination_basis=basis,
            payroll_input=payroll_input,
            period=period,
        )
    ]


async def require_matching_severance_source_for_existing(
    db,
    *,
    company_id,
    payroll_input_id,
    calculation_id,
):
    """Do not silently reuse a calculation with stale severance."""

    latest = await db.scalar(
        select(PayrollTerminationSeverance.id)
        .where(
            PayrollTerminationSeverance.company_id == company_id,
            PayrollTerminationSeverance.payroll_input_id
            == payroll_input_id,
        )
        .order_by(
            PayrollTerminationSeverance.revision.desc()
        )
        .limit(1)
    )

    saved = await db.scalar(
        select(
            PayrollCalculationLine.source_termination_severance_id
        ).where(
            PayrollCalculationLine.company_id == company_id,
            PayrollCalculationLine.payroll_calculation_id
            == calculation_id,
            PayrollCalculationLine.line_type == "severance",
        ).limit(1)
    )

    if latest != saved:
        raise SeveranceLineError(
            "Severance changed after payroll calculation; "
            "a documented correction is required"
        )
