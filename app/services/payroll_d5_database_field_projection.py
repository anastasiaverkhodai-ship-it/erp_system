"""Read-only database-bound D5 candidate projection.

Only persisted, active ERP events with matching identity,
contract, position and professional evidence may be projected.

This is NOT an approved statutory D5 report generator.
"""

from datetime import date
from hashlib import sha256
import json

from sqlalchemy import select

from app.models.employee import Employee
from app.models.employment_contract import EmploymentContract
from app.models.employment_event import EmploymentEvent
from app.models.payroll_d5_professional_evidence import (
    PayrollD5ProfessionalEvidence,
)
from app.models.position import Position

from app.services.payroll_d5_event_sources import (
    collect_d5_event_sources,
)
from app.services.payroll_d5_event_field_candidate import (
    D5EventFacts,
    project_d5_event_candidate,
)


class D5DatabaseProjectionError(ValueError):
    pass


CATEGORIES = {
    "primary": 1,
    "internal_secondary": 2,
    "external_secondary": 2,
}


def _positive(value, name):
    if type(value) is not int or value <= 0:
        raise D5DatabaseProjectionError(
            f"Invalid {name}"
        )


def _date(value, name):
    if type(value) is not date:
        raise D5DatabaseProjectionError(
            f"Invalid {name}"
        )


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise D5DatabaseProjectionError(
            f"Missing {name}"
        )
    return value.strip()


def _canonical_digest(payload):
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=lambda value: (
            value.isoformat()
            if hasattr(value, "isoformat")
            else str(value)
        ),
    ).encode("utf-8")
    return sha256(raw).hexdigest()


def _termination_reference(source):
    if source.event_type != "termination":
        return None

    basis = source.termination_basis

    if not isinstance(basis, dict):
        raise D5DatabaseProjectionError(
            "Structured termination basis required"
        )

    legal = _text(
        basis.get("labour_code_provision"),
        "labour_code_provision",
    )

    reference = _text(
        basis.get("source_reference"),
        "termination_source_reference",
    )

    # Candidate-only representation.
    return legal + " | " + reference


async def project_persisted_d5_event(
    db,
    *,
    company_id,
    employment_event_id,
):
    """Project one actual ERP event without writing report rows."""

    _positive(company_id, "company_id")
    _positive(employment_event_id, "employment_event_id")

    event = await db.scalar(
        select(EmploymentEvent).where(
            EmploymentEvent.company_id == company_id,
            EmploymentEvent.id == employment_event_id,
        )
    )

    if event is None:
        raise D5DatabaseProjectionError(
            "Persisted HR event not found"
        )

    if event.event_type not in (
        "hire", "transfer", "termination"
    ):
        raise D5DatabaseProjectionError(
            "Unsupported or reversal HR event"
        )

    _date(event.effective_date, "effective_date")

    active_sources = await collect_d5_event_sources(
        db,
        company_id=company_id,
        date_from=event.effective_date,
        date_to=event.effective_date,
    )

    matches = [
        item for item in active_sources
        if item.employment_event_id == employment_event_id
    ]

    if len(matches) != 1:
        raise D5DatabaseProjectionError(
            "HR event missing from active, non-reversed source"
        )

    source = matches[0]

    if (
        source.employment_contract_id
        != event.employment_contract_id
        or source.event_type != event.event_type
        or source.effective_date != event.effective_date
        or source.order_number != event.order_number
    ):
        raise D5DatabaseProjectionError(
            "Source snapshot mismatch"
        )

    contract = await db.scalar(
        select(EmploymentContract).where(
            EmploymentContract.company_id == company_id,
            EmploymentContract.id == event.employment_contract_id,
        )
    )

    if contract is None:
        raise D5DatabaseProjectionError(
            "Persisted employment contract not found"
        )

    employee = await db.scalar(
        select(Employee).where(
            Employee.company_id == company_id,
            Employee.id == contract.employee_id,
        )
    )

    if employee is None:
        raise D5DatabaseProjectionError(
            "Persisted employee not found"
        )

    if event.effective_date < contract.start_date:
        raise D5DatabaseProjectionError(
            "Event predates employment contract"
        )

    category = CATEGORIES.get(contract.employment_kind)

    if category is None:
        raise D5DatabaseProjectionError(
            "Employment kind requires explicit review"
        )

    evidence_rows = (
        await db.scalars(
            select(PayrollD5ProfessionalEvidence).where(
                PayrollD5ProfessionalEvidence.company_id
                == company_id,
                PayrollD5ProfessionalEvidence.employee_id
                == employee.id,
                PayrollD5ProfessionalEvidence.employment_contract_id
                == contract.id,
                PayrollD5ProfessionalEvidence.employment_event_id
                == event.id,
            )
        )
    ).all()

    if len(evidence_rows) != 1:
        raise D5DatabaseProjectionError(
            "Exactly one persisted professional evidence required"
        )

    evidence = evidence_rows[0]

    if evidence.employment_category != contract.employment_kind:
        raise D5DatabaseProjectionError(
            "Employment category evidence mismatch"
        )

    if (
        evidence.effective_from > event.effective_date
        or (
            evidence.effective_to is not None
            and evidence.effective_to < event.effective_date
        )
        or evidence.documented_on > event.effective_date
    ):
        raise D5DatabaseProjectionError(
            "Professional evidence not effective on event date"
        )

    position = await db.scalar(
        select(Position).where(
            Position.company_id == company_id,
            Position.id == evidence.position_id,
        )
    )

    if position is None:
        raise D5DatabaseProjectionError(
            "Persisted historical position missing"
        )

    if (
        contract.position_id is not None
        and contract.position_id != evidence.position_id
    ):
        raise D5DatabaseProjectionError(
            "Contract position mismatch"
        )

    after = event.after_state

    if (
        isinstance(after, dict)
        and after.get("position_id") is not None
        and after["position_id"] != evidence.position_id
    ):
        raise D5DatabaseProjectionError(
            "Event position snapshot mismatch"
        )

    if position.name != evidence.position_name:
        raise D5DatabaseProjectionError(
            "Position title differs from persisted evidence"
        )

    identity = _text(
        employee.tax_number,
        "employee tax identity",
    )

    full_name = " ".join(
        part.strip()
        for part in (
            employee.last_name,
            employee.first_name,
            employee.middle_name,
        )
        if isinstance(part, str) and part.strip()
    )

    _text(full_name, "employee full name")

    order = _text(
        event.order_number,
        "employment order",
    )

    _text(
        evidence.evidence_reference,
        "professional evidence reference",
    )

    # Digest is of persisted ERP evidence, not the
    # statutory source document or approved classifier.
    source_digest = _canonical_digest({
        "company_id": company_id,
        "event_id": event.id,
        "contract_id": contract.id,
        "employee_id": employee.id,
        "event_kind": event.event_type,
        "event_date": event.effective_date,
        "order_number": event.order_number,
        "order_date": event.order_date,
        "before_state": event.before_state,
        "after_state": event.after_state,
        "request_payload": event.request_payload,
        "professional_evidence_id": evidence.id,
        "position_id": position.id,
        "profession_code": evidence.profession_code,
        "professional_name": evidence.professional_name,
        "position_name": evidence.position_name,
        "evidence_reference": evidence.evidence_reference,
        "effective_from": evidence.effective_from,
        "effective_to": evidence.effective_to,
        "documented_on": evidence.documented_on,
        "employment_kind": contract.employment_kind,
        "employee_tax_number": identity,
        "employee_full_name": full_name,
    })

    facts = D5EventFacts(
        company_id=company_id,
        employee_id=employee.id,
        contract_id=contract.id,
        employment_event_id=event.id,
        event_kind=event.event_type,
        effective_date=event.effective_date,
        person_category=category,
        confirmed_identity=identity,
        full_name=full_name,
        profession_code=evidence.profession_code,
        profession_name=evidence.professional_name,
        position_title=evidence.position_name,
        order_reference=order,
        source_sha256=source_digest,
        termination_basis=_termination_reference(source),
    )

    candidate = project_d5_event_candidate(facts)

    return {
        "company_id": company_id,
        "employment_event_id": event.id,
        "professional_evidence_id": evidence.id,
        "persisted_erp_evidence_sha256": source_digest,
        "candidate": candidate,
        "source_binding": "PERSISTED_POSTGRESQL_RECORDS_VERIFIED",
        "normative_document_verified": False,
        "classifier_legally_approved": False,
        "statutory_field_mapping_approved": False,
        "official_rows_generated": 0,
        "official_generation": "BLOCKED",
        "review_only": True,
    }


async def review_persisted_d5_event_period_sources(
    db,
    *,
    company_id,
    employment_event_id,
    normative_candidates,
):
    """Join persisted HR evidence to date-effective source candidates.

    This operation performs internal review only. Source metadata
    cannot establish official document authenticity or approve D5.
    """
    from app.services.payroll_d5_normative_acceptance import (
        review_d5_sources_for_period,
    )

    persisted = await project_persisted_d5_event(
        db,
        company_id=company_id,
        employment_event_id=employment_event_id,
    )

    if persisted.get("source_binding") != (
        "PERSISTED_POSTGRESQL_RECORDS_VERIFIED"
    ):
        raise D5DatabaseProjectionError(
            "Verified persisted HR evidence required"
        )

    if persisted.get("official_generation") != "BLOCKED":
        raise D5DatabaseProjectionError(
            "Unexpected statutory generation state"
        )

    identity = persisted["candidate"]["identity"]

    if identity.get("company_id") != company_id:
        raise D5DatabaseProjectionError(
            "Company identity mismatch"
        )

    if identity.get("employment_event_id") != employment_event_id:
        raise D5DatabaseProjectionError(
            "Employment event identity mismatch"
        )

    event_date = date.fromisoformat(
        identity["effective_date"]
    )

    review = review_d5_sources_for_period(
        normative_candidates,
        event_date,
    )

    missing = review["missing_source_kinds"]
    ambiguous = review["ambiguous_source_kinds"]
    conflicting = review["conflicting_document_ids"]

    complete = not (
        missing or ambiguous or conflicting
    )

    return {
        "company_id": company_id,
        "employment_event_id": employment_event_id,
        "event_effective_date": event_date.isoformat(),
        "persisted_erp_evidence_sha256": (
            persisted["persisted_erp_evidence_sha256"]
        ),
        "candidate_sha256": (
            persisted["candidate"]["candidate_sha256"]
        ),
        "source_binding": persisted["source_binding"],
        "normative_source_review": review,
        "period_source_coverage": (
            "COMPLETE_CANDIDATE_COVERAGE"
            if complete else "REVIEW_BLOCKED"
        ),
        "independent_normative_approval": "NOT_VERIFIED",
        "normative_mapping": "NOT_APPROVED",
        "official_generation": "BLOCKED",
        "official_rows_generated": 0,
    }


async def review_persisted_d5_profession_history(
    db,
    *,
    company_id,
    employment_event_id,
    profession_changes,
):
    """Bind persisted PostgreSQL HR evidence to candidate history."""
    from app.services.payroll_d5_profession_effective_history import (
        review_d5_profession_history,
    )

    persisted = await project_persisted_d5_event(
        db,
        company_id=company_id,
        employment_event_id=employment_event_id,
    )

    if persisted.get("source_binding") != (
        "PERSISTED_POSTGRESQL_RECORDS_VERIFIED"
    ):
        raise D5DatabaseProjectionError(
            "Persisted HR source verification required"
        )

    if persisted.get("official_generation") != "BLOCKED":
        raise D5DatabaseProjectionError(
            "Official generation must remain blocked"
        )

    candidate = persisted["candidate"]
    identity = candidate["identity"]

    if (
        identity.get("company_id") != company_id
        or identity.get("employment_event_id")
        != employment_event_id
    ):
        raise D5DatabaseProjectionError(
            "Persisted HR identity mismatch"
        )

    history = review_d5_profession_history(
        profession_code=candidate["fields"]["13"],
        reporting_date=date.fromisoformat(
            identity["effective_date"]
        ),
        changes=profession_changes,
    )

    return {
        "company_id": company_id,
        "employment_event_id": employment_event_id,
        "persisted_erp_evidence_sha256": (
            persisted["persisted_erp_evidence_sha256"]
        ),
        "candidate_sha256": candidate["candidate_sha256"],
        "source_binding": persisted["source_binding"],
        "profession_history": history,
        "profession_date_review": (
            "CANDIDATE_REVIEW"
            if history["candidate_state"] == "CANDIDATE_ACTIVE"
            else "REVIEW_BLOCKED"
        ),
        "normative_approval": "NOT_APPROVED",
        "official_generation": "BLOCKED",
        "official_rows_generated": 0,
    }
