"""Create the first immutable, verified severance source.

No implicit correction, commit, accounting posting or payment.
Call exclusively within a company-authorized payroll transaction.
"""

from sqlalchemy import select

from app.models.employment_event import EmploymentEvent
from app.models.payroll import PayrollInput
from app.models.payroll_termination_severance import (
    PayrollTerminationSeverance,
)
from app.schemas.employment_termination_basis import (
    EmploymentTerminationBasis,
)
from app.services.payroll_severance_average_earnings import (
    SeveranceAverageError,
)
from app.services.payroll_severance_verified_basis_service import (
    prepare_verified_severance_basis,
)


class VerifiedSeveranceSourceError(SeveranceAverageError):
    pass


def require_request_key(value):
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > 200
    ):
        raise VerifiedSeveranceSourceError(
            "Valid severance request key required"
        )
    return value.strip()


def require_initial_source_absent(rows, request_key):
    """Reject silent overwrite, replay and branching corrections."""
    if any(row.request_key == request_key for row in rows):
        raise VerifiedSeveranceSourceError(
            "Existing request requires explicit idempotency verification"
        )
    if rows:
        raise VerifiedSeveranceSourceError(
            "Termination already has a severance source; "
            "use an explicit documented correction"
        )


def validate_source_identity(
    *,
    basis,
    payroll_input,
    event,
    termination_basis,
    company_id,
    employment_contract_id,
):
    if (
        payroll_input.company_id != company_id
        or payroll_input.employment_contract_id
        != employment_contract_id
        or event.company_id != company_id
        or event.employment_contract_id
        != employment_contract_id
        or event.id != basis.termination_event_id
    ):
        raise VerifiedSeveranceSourceError(
            "Payroll and termination identity mismatch"
        )

    if (
        basis.company_id != company_id
        or basis.employment_contract_id
        != employment_contract_id
        or event.effective_date != basis.termination_date
        or event.order_number != basis.order_number
    ):
        raise VerifiedSeveranceSourceError(
            "Verified basis differs from termination evidence"
        )

    if (
        event.event_type != "termination"
        and getattr(event.event_type, "value", None)
        != "termination"
    ):
        raise VerifiedSeveranceSourceError(
            "Termination event required"
        )

    if not (
        termination_basis.labour_code_provision
        and termination_basis.source_reference
        and termination_basis.ground
    ):
        raise VerifiedSeveranceSourceError(
            "Documented statutory termination ground required"
        )


def verify_exact_initial_replay(
    row,
    *,
    basis,
    policy,
    termination_basis,
    payroll_input_id,
    created_by,
):
    """Return an existing initial source only for an exact replay.

    A different payload or superseded source must use a documented
    correction, never a silent replacement.
    """
    expected = {
        "company_id": basis.company_id,
        "employment_contract_id": basis.employment_contract_id,
        "payroll_input_id": payroll_input_id,
        "termination_event_id": basis.termination_event_id,
        "revision": 1,
        "supersedes_id": None,
        "termination_date": basis.termination_date,
        "order_number": basis.order_number,
        "policy_ground": termination_basis.ground,
        "policy_category": policy.category,
        "labour_code_provision": (
            termination_basis.labour_code_provision
        ),
        "source_reference": termination_basis.source_reference,
        "multiplier": policy.multiplier,
        "basis_as_of": basis.basis_as_of,
        "basis_reference": basis.basis_reference,
        "base_amount": basis.base_amount,
        "gross_amount": basis.gross_amount,
        "currency_code": "UAH",
        "created_by": created_by,
    }

    different = [
        field
        for field, value in expected.items()
        if getattr(row, field, object()) != value
    ]

    if different:
        raise VerifiedSeveranceSourceError(
            "Existing severance request does not match verified "
            "evidence: " + ", ".join(different)
        )

    return row


async def create_initial_verified_severance_source(
    db,
    *,
    company_id: int,
    employment_contract_id: int,
    payroll_input_id: int,
    request_key: str,
    created_by: int,
):
    """Create revision one; caller controls transaction commit.

    Locks the termination evidence, then rechecks for an existing
    source. Database uniqueness remains the final concurrency guard.
    """
    key = require_request_key(request_key)

    for label, value in (
        ("company_id", company_id),
        ("employment_contract_id", employment_contract_id),
        ("payroll_input_id", payroll_input_id),
        ("created_by", created_by),
    ):
        if type(value) is not int or value <= 0:
            raise VerifiedSeveranceSourceError(
                f"Invalid {label}"
            )

    payroll_input = await db.scalar(
        select(PayrollInput).where(
            PayrollInput.company_id == company_id,
            PayrollInput.id == payroll_input_id,
            PayrollInput.employment_contract_id
            == employment_contract_id,
        )
    )
    if payroll_input is None:
        raise VerifiedSeveranceSourceError(
            "Company-scoped payroll input not found"
        )

    # Server derives the legal and average-earnings basis.
    basis = await prepare_verified_severance_basis(
        db,
        company_id=company_id,
        employment_contract_id=employment_contract_id,
        payroll_input_id=payroll_input_id,
    )

    event = await db.scalar(
        select(EmploymentEvent)
        .where(
            EmploymentEvent.company_id == company_id,
            EmploymentEvent.id == basis.termination_event_id,
        )
        .with_for_update()
    )
    if event is None:
        raise VerifiedSeveranceSourceError(
            "Documented termination event not found"
        )

    existing = list((
        await db.scalars(
            select(PayrollTerminationSeverance).where(
                PayrollTerminationSeverance.company_id
                == company_id,
                PayrollTerminationSeverance.termination_event_id
                == event.id,
            )
        )
    ).all())

    matching = [
        row for row in existing if row.request_key == key
    ]
    if not matching:
        require_initial_source_absent(existing, key)


    # Also forbid reusing a request key for another event.
    reused = await db.scalar(
        select(PayrollTerminationSeverance.id).where(
            PayrollTerminationSeverance.company_id
            == company_id,
            PayrollTerminationSeverance.request_key == key,
        ).limit(1)
    )
    if reused is not None and (
        not matching
        or len(matching) != 1
        or reused != matching[0].id
    ):
        raise VerifiedSeveranceSourceError(
            "Severance request key already belongs to another source"
        )

    termination_basis = EmploymentTerminationBasis.model_validate(
        event.termination_basis
    )

    validate_source_identity(
        basis=basis,
        payroll_input=payroll_input,
        event=event,
        termination_basis=termination_basis,
        company_id=company_id,
        employment_contract_id=employment_contract_id,
    )

    # Compare policy against original immutable legal evidence.
    from app.services.payroll_termination_severance_policy import (
        classify_severance_basis,
    )
    policy = classify_severance_basis(termination_basis)

    if (
        policy.category != basis.policy_category
        or policy.multiplier != basis.multiplier
        or policy.category != "average_months"
    ):
        raise VerifiedSeveranceSourceError(
            "Legal policy changed after basis preparation"
        )

    if matching:
        if len(existing) != 1 or len(matching) != 1:
            raise VerifiedSeveranceSourceError(
                "Existing severance has revisions; "
                "initial replay cannot replace a correction"
            )

        return verify_exact_initial_replay(
            matching[0],
            basis=basis,
            policy=policy,
            termination_basis=termination_basis,
            payroll_input_id=payroll_input_id,
            created_by=created_by,
        )

    row = PayrollTerminationSeverance(
        company_id=company_id,
        payroll_input_id=payroll_input_id,
        employment_contract_id=employment_contract_id,
        termination_event_id=event.id,
        revision=1,
        supersedes_id=None,
        request_key=key,
        termination_date=basis.termination_date,
        order_number=basis.order_number,
        policy_ground=termination_basis.ground,
        policy_category=policy.category,
        labour_code_provision=(
            termination_basis.labour_code_provision
        ),
        source_reference=termination_basis.source_reference,
        multiplier=policy.multiplier,
        basis_as_of=basis.basis_as_of,
        basis_reference=basis.basis_reference,
        base_amount=basis.base_amount,
        gross_amount=basis.gross_amount,
        currency_code="UAH",
        created_by=created_by,
    )

    db.add(row)
    await db.flush()
    return row
