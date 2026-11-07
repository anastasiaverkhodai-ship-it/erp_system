"""Read-only historical category transition review for D5.

No statutory approval, filing, or official D5 rows.
"""

from datetime import timedelta

from sqlalchemy import select

from app.models.employee import Employee
from app.models.employment_contract import EmploymentContract
from app.models.employment_event import EmploymentEvent
from app.models.position import Position
from app.models.payroll_d5_professional_evidence import (
    PayrollD5ProfessionalEvidence,
)
from app.services.payroll_d5_database_field_projection import (
    D5DatabaseProjectionError,
    _canonical_digest,
    project_persisted_d5_event,
)
from app.services.payroll_d5_event_field_candidate import (
    D5CategoryChangeFacts,
    D5EventFacts,
    project_d5_category_change_candidate,
)


class D5CategoryTransitionError(ValueError):
    pass


CATEGORY = {
    "primary": 1,
    "internal_secondary": 2,
    "external_secondary": 2,
}


def _positive(value, label):
    if type(value) is not int or value <= 0:
        raise D5CategoryTransitionError(
            "Invalid " + label
        )


def _kind(value):
    if value not in CATEGORY:
        raise D5CategoryTransitionError(
            "Unverified employment category"
        )
    return value


def _states(event, contract):
    before = event.before_state
    after = event.after_state

    if not isinstance(before, dict) or not isinstance(after, dict):
        raise D5CategoryTransitionError(
            "Historical event states required"
        )

    old_kind = _kind(before.get("employment_kind"))
    new_kind = _kind(after.get("employment_kind"))

    if old_kind == new_kind:
        raise D5CategoryTransitionError(
            "No employment category change"
        )

    if CATEGORY[old_kind] == CATEGORY[new_kind]:
        raise D5CategoryTransitionError(
            "No D5 category 1/2 transition"
        )

    if contract.employment_kind != new_kind:
        raise D5CategoryTransitionError(
            "Current contract category conflicts with event"
        )

    for state, label in (
        (before, "before"),
        (after, "after"),
    ):
        value = state.get("position_id")
        _positive(value, label + "_position_id")

    return old_kind, new_kind


async def project_persisted_d5_category_transition(
    db,
    *,
    company_id,
    employment_event_id,
    previous_evidence_id,
):
    """Produce two review candidates from persisted historic evidence."""

    for value, name in (
        (company_id, "company_id"),
        (employment_event_id, "employment_event_id"),
        (previous_evidence_id, "previous_evidence_id"),
    ):
        _positive(value, name)

    event = await db.scalar(
        select(EmploymentEvent).where(
            EmploymentEvent.id == employment_event_id,
            EmploymentEvent.company_id == company_id,
        )
    )

    if event is None or event.event_type != "transfer":
        raise D5CategoryTransitionError(
            "Persisted transfer event required"
        )

    # Existing projector already excludes reversal records
    # and validates the current professional evidence.
    try:
        current = await project_persisted_d5_event(
            db,
            company_id=company_id,
            employment_event_id=employment_event_id,
        )
    except D5DatabaseProjectionError as exc:
        raise D5CategoryTransitionError(
            'Current active HR source not verified: ' + str(exc)
        ) from exc

    contract = await db.scalar(
        select(EmploymentContract).where(
            EmploymentContract.id == event.employment_contract_id,
            EmploymentContract.company_id == company_id,
        )
    )

    if contract is None:
        raise D5CategoryTransitionError(
            "Contract not found"
        )

    old_kind, new_kind = _states(event, contract)

    employee = await db.scalar(
        select(Employee).where(
            Employee.id == contract.employee_id,
            Employee.company_id == company_id,
        )
    )

    if employee is None:
        raise D5CategoryTransitionError(
            "Employee not found"
        )

    old = await db.scalar(
        select(PayrollD5ProfessionalEvidence).where(
            PayrollD5ProfessionalEvidence.id
            == previous_evidence_id,
            PayrollD5ProfessionalEvidence.company_id
            == company_id,
            PayrollD5ProfessionalEvidence.employee_id
            == employee.id,
            PayrollD5ProfessionalEvidence.employment_contract_id
            == contract.id,
        )
    )

    if old is None:
        raise D5CategoryTransitionError(
            "Previous professional evidence not found"
        )

    if old.employment_event_id == event.id:
        raise D5CategoryTransitionError(
            "Previous evidence must belong to an earlier event"
        )

    previous_event = await db.scalar(
        select(EmploymentEvent).where(
            EmploymentEvent.id == old.employment_event_id,
            EmploymentEvent.company_id == company_id,
            EmploymentEvent.employment_contract_id == contract.id,
        )
    )

    if (
        previous_event is None
        or previous_event.event_type not in (
            "hire", "transfer"
        )
    ):
        raise D5CategoryTransitionError(
            "Earlier HR event is not verified"
        )

    if previous_event.effective_date >= event.effective_date:
        raise D5CategoryTransitionError(
            "Previous event is not earlier than transfer"
        )

    reversal = await db.scalar(
        select(EmploymentEvent.id).where(
            EmploymentEvent.company_id == company_id,
            EmploymentEvent.event_type == "reversal",
            EmploymentEvent.reversal_of_id
            == previous_event.id,
        )
    )

    if reversal is not None:
        raise D5CategoryTransitionError(
            "Previous HR event has been reversed"
        )

    if old.employment_category != old_kind:
        raise D5CategoryTransitionError(
            "Previous employment category mismatch"
        )

    if old.position_id != event.before_state["position_id"]:
        raise D5CategoryTransitionError(
            "Previous position snapshot mismatch"
        )

    if (
        old.effective_from > previous_event.effective_date
        or old.documented_on > previous_event.effective_date
        or old.effective_from > event.effective_date
    ):
        raise D5CategoryTransitionError(
            "Previous professional history is not effective"
        )

    # Both an inclusive transition date and the preceding
    # day are accepted for candidate review. The final
    # statutory date convention requires independent approval.
    accepted_end_dates = {
        event.effective_date,
        event.effective_date - timedelta(days=1),
    }

    if old.effective_to not in accepted_end_dates:
        raise D5CategoryTransitionError(
            "Previous professional interval not closed at transition"
        )

    old_position = await db.scalar(
        select(Position).where(
            Position.id == old.position_id,
            Position.company_id == company_id,
        )
    )

    if (
        old_position is None
        or old_position.name != old.position_name
    ):
        raise D5CategoryTransitionError(
            "Previous persisted position mismatch"
        )

    present = current["candidate"]
    new_fields = present["fields"]

    if (
        new_fields["07"] != CATEGORY[new_kind]
        or new_fields["12"] != 1
    ):
        raise D5CategoryTransitionError(
            "Current candidate transition mismatch"
        )

    if (
        event.after_state["position_id"]
        != contract.position_id
    ):
        raise D5CategoryTransitionError(
            "Current event position snapshot mismatch"
        )

    name = new_fields["09"]
    identity = new_fields["08"]

    old_digest = _canonical_digest({
        "company_id": company_id,
        "contract_id": contract.id,
        "transfer_event_id": event.id,
        "previous_event_id": previous_event.id,
        "previous_evidence_id": old.id,
        "previous_employment_category": old_kind,
        "previous_position_id": old.position_id,
        "previous_profession_code": old.profession_code,
        "previous_professional_name": old.professional_name,
        "previous_position_name": old.position_name,
        "previous_evidence_reference": old.evidence_reference,
        "previous_interval_start": old.effective_from,
        "previous_interval_end": old.effective_to,
        "previous_order": previous_event.order_number,
        "transfer_order": event.order_number,
    })

    previous_facts = D5EventFacts(
        company_id=company_id,
        employee_id=employee.id,
        contract_id=contract.id,
        employment_event_id=event.id,
        event_kind="transfer",
        effective_date=event.effective_date,
        person_category=CATEGORY[old_kind],
        confirmed_identity=identity,
        full_name=name,
        profession_code=old.profession_code,
        profession_name=old.professional_name,
        position_title=old.position_name,
        order_reference=event.order_number,
        source_sha256=old_digest,
    )

    current_facts = D5EventFacts(
        company_id=company_id,
        employee_id=employee.id,
        contract_id=contract.id,
        employment_event_id=event.id,
        event_kind="transfer",
        effective_date=event.effective_date,
        person_category=CATEGORY[new_kind],
        confirmed_identity=identity,
        full_name=name,
        profession_code=new_fields["13"],
        profession_name=new_fields["14"],
        position_title=new_fields["15"],
        order_reference=event.order_number,
        source_sha256=current[
            "persisted_erp_evidence_sha256"
        ],
    )

    pair = project_d5_category_change_candidate(
        D5CategoryChangeFacts(
            previous=previous_facts,
            current=current_facts,
            previous_end_date=event.effective_date,
            current_start_date=event.effective_date,
        )
    )

    return {
        "company_id": company_id,
        "transfer_event_id": event.id,
        "previous_event_id": previous_event.id,
        "previous_professional_evidence_id": old.id,
        "current_professional_evidence_id": current[
            "professional_evidence_id"
        ],
        "previous_employment_kind": old_kind,
        "current_employment_kind": new_kind,
        "historical_category_sources": "PERSISTED_POSTGRESQL",
        "previous_evidence_sha256": old_digest,
        "current_evidence_sha256": current[
            "persisted_erp_evidence_sha256"
        ],
        "candidate": pair,
        "review_only": True,
        "statutory_date_convention_approved": False,
        "legal_classifier_approved": False,
        "normative_mapping_approved": False,
        "official_rows_generated": 0,
        "official_generation": "BLOCKED",
    }
