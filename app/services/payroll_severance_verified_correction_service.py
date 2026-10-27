"""Documented, immutable corrections of severance source evidence.

Requires a fresh ERP-derived calculation. Does not commit, post,
pay or update previously persisted severance versions.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import select

from app.models.payroll_termination_severance import (
    PayrollTerminationSeverance,
)
from app.models.employment_event import EmploymentEvent
from app.models.payroll import PayrollInput

from app.services.payroll_severance_verified_basis_service import (
    prepare_verified_severance_basis,
)
from app.services.payroll_severance_verified_source_service import (
    VerifiedSeveranceSourceError,
    require_request_key,
)


@dataclass(frozen=True)
class CorrectionEvidence:
    reference: str
    effective_date: date


def require_correction_evidence(evidence):
    if not isinstance(evidence, CorrectionEvidence):
        raise VerifiedSeveranceSourceError(
            "Typed correction evidence required"
        )

    if (
        not isinstance(evidence.reference, str)
        or len(evidence.reference.strip()) < 8
        or len(evidence.reference) > 1000
    ):
        raise VerifiedSeveranceSourceError(
            "Documented correction reference required"
        )

    if type(evidence.effective_date) is not date:
        raise VerifiedSeveranceSourceError(
            "Documented correction date required"
        )

    return evidence


def validate_new_revision(
    previous,
    basis,
    evidence,
    *,
    company_id,
    employment_contract_id,
    payroll_input_id,
):
    require_correction_evidence(evidence)

    if (
        previous.company_id != company_id
        or previous.employment_contract_id
        != employment_contract_id
        or previous.payroll_input_id != payroll_input_id
        or basis.company_id != company_id
        or basis.employment_contract_id
        != employment_contract_id
        or basis.termination_event_id
        != previous.termination_event_id
    ):
        raise VerifiedSeveranceSourceError(
            "Correction scope or source identity mismatch"
        )

    if (
        basis.termination_date != previous.termination_date
        or basis.order_number != previous.order_number
        or basis.policy_category != previous.policy_category
        or basis.multiplier != previous.multiplier
    ):
        raise VerifiedSeveranceSourceError(
            "Correction changes immutable termination identity"
        )

    if evidence.effective_date < previous.termination_date:
        raise VerifiedSeveranceSourceError(
            "Correction evidence predates termination"
        )

    if (
        not isinstance(basis.base_amount, Decimal)
        or not basis.base_amount.is_finite()
        or basis.base_amount <= 0
        or not isinstance(basis.gross_amount, Decimal)
        or not basis.gross_amount.is_finite()
        or basis.gross_amount <= 0
    ):
        raise VerifiedSeveranceSourceError(
            "Invalid corrected statutory amount"
        )

    if (
        basis.base_amount == previous.base_amount
        and basis.gross_amount == previous.gross_amount
    ):
        raise VerifiedSeveranceSourceError(
            "No changed verified source evidence"
        )

    if (
        not isinstance(basis.basis_reference, str)
        or not basis.basis_reference.strip()
        or basis.basis_reference == previous.basis_reference
    ):
        raise VerifiedSeveranceSourceError(
            "Corrected amount requires changed verified source reference"
        )



async def create_verified_severance_correction(
    db,
    *,
    company_id: int,
    employment_contract_id: int,
    payroll_input_id: int,
    previous_source_id: int,
    request_key: str,
    created_by: int,
    evidence: CorrectionEvidence,
):
    """Append one verified revision while holding the event row lock."""

    key = require_request_key(request_key)
    require_correction_evidence(evidence)

    for name, value in (
        ("company_id", company_id),
        ("employment_contract_id", employment_contract_id),
        ("payroll_input_id", payroll_input_id),
        ("previous_source_id", previous_source_id),
        ("created_by", created_by),
    ):
        if type(value) is not int or value <= 0:
            raise VerifiedSeveranceSourceError(
                f"Invalid {name}"
            )

    previous = await db.scalar(
        select(PayrollTerminationSeverance).where(
            PayrollTerminationSeverance.company_id == company_id,
            PayrollTerminationSeverance.id == previous_source_id,
        )
    )
    if previous is None:
        raise VerifiedSeveranceSourceError(
            "Previous severance source not found"
        )

    event = await db.scalar(
        select(EmploymentEvent).where(
            EmploymentEvent.company_id == company_id,
            EmploymentEvent.id == previous.termination_event_id,
        ).with_for_update()
    )
    if event is None:
        raise VerifiedSeveranceSourceError(
            "Termination event not found"
        )

    # Serialize corrections after obtaining the event lock.
    rows = list((
        await db.scalars(
            select(PayrollTerminationSeverance).where(
                PayrollTerminationSeverance.company_id == company_id,
                PayrollTerminationSeverance.termination_event_id
                == previous.termination_event_id,
            ).order_by(PayrollTerminationSeverance.revision)
        )
    ).all())

    # Idempotent correction replay is evaluated under the same
    # termination-event row lock as new correction creation.
    matching = [row for row in rows if row.request_key == key]

    if matching:
        if len(matching) != 1:
            raise VerifiedSeveranceSourceError(
                "Existing correction request requires exact replay"
            )

        stored = matching[0]

        # A replay names the ORIGINAL superseded source. It must not
        # reinterpret a correction request as a new branch.
        if (
            stored.id != rows[-1].id
            or stored.supersedes_id != previous.id
            or stored.revision != previous.revision + 1
            or stored.company_id != company_id
            or stored.employment_contract_id != employment_contract_id
            or stored.payroll_input_id != payroll_input_id
            or stored.termination_event_id != previous.termination_event_id
            or stored.created_by != created_by
        ):
            raise VerifiedSeveranceSourceError(
                "Existing correction request requires exact replay"
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

        # Recheck the present legal basis. A request cannot replay
        # a superseded or no-longer-current earnings snapshot.
        basis = await prepare_verified_severance_basis(
            db,
            company_id=company_id,
            employment_contract_id=employment_contract_id,
            payroll_input_id=payroll_input_id,
        )

        exact_reference = (
            f"CORRECTION:{evidence.effective_date.isoformat()}:"
            f"{evidence.reference.strip()};"
            f"{basis.basis_reference}"
        )

        if (
            stored.basis_reference != exact_reference
            or stored.basis_as_of != basis.basis_as_of
            or stored.base_amount != basis.base_amount
            or stored.gross_amount != basis.gross_amount
            or stored.currency_code != basis.currency_code
            or stored.termination_date != previous.termination_date
            or stored.order_number != previous.order_number
            or stored.policy_ground != previous.policy_ground
            or stored.policy_category != previous.policy_category
            or stored.labour_code_provision
            != previous.labour_code_provision
            or stored.source_reference != previous.source_reference
            or stored.multiplier != previous.multiplier
        ):
            raise VerifiedSeveranceSourceError(
                "Existing correction request requires exact replay"
            )

        # No INSERT, UPDATE or flush: return the existing revision.
        return stored

    if not rows or rows[-1].id != previous.id:
        raise VerifiedSeveranceSourceError(
            "Correction must supersede the latest source"
        )

    if any(row.supersedes_id == previous.id for row in rows):
        raise VerifiedSeveranceSourceError(
            "Correction branch already exists"
        )

    reused = await db.scalar(
        select(PayrollTerminationSeverance.id).where(
            PayrollTerminationSeverance.company_id == company_id,
            PayrollTerminationSeverance.request_key == key,
        ).limit(1)
    )
    if reused is not None:
        raise VerifiedSeveranceSourceError(
            "Correction request key already used"
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

    basis = await prepare_verified_severance_basis(
        db,
        company_id=company_id,
        employment_contract_id=employment_contract_id,
        payroll_input_id=payroll_input_id,
    )

    validate_new_revision(
        previous,
        basis,
        evidence,
        company_id=company_id,
        employment_contract_id=employment_contract_id,
        payroll_input_id=payroll_input_id,
    )

    reference = (
        f"CORRECTION:{evidence.effective_date.isoformat()}:"
        f"{evidence.reference.strip()};"
        f"{basis.basis_reference}"
    )

    if len(reference) > 1000:
        raise VerifiedSeveranceSourceError(
            "Correction reference exceeds database limit"
        )

    row = PayrollTerminationSeverance(
        company_id=company_id,
        payroll_input_id=payroll_input_id,
        employment_contract_id=employment_contract_id,
        termination_event_id=previous.termination_event_id,
        revision=previous.revision + 1,
        supersedes_id=previous.id,
        request_key=key,
        termination_date=previous.termination_date,
        order_number=previous.order_number,
        policy_ground=previous.policy_ground,
        policy_category=previous.policy_category,
        labour_code_provision=previous.labour_code_provision,
        source_reference=previous.source_reference,
        multiplier=previous.multiplier,
        basis_as_of=basis.basis_as_of,
        basis_reference=reference,
        base_amount=basis.base_amount,
        gross_amount=basis.gross_amount,
        currency_code="UAH",
        created_by=created_by,
    )

    db.add(row)
    await db.flush()

    return row
