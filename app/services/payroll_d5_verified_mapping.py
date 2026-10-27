
"""Internal D5 mapping candidate; NOT an official D5 submission row.

Only hire, permanent transfer, and termination are currently represented.
Never mark regulatory readiness true from these records alone.
"""
from dataclasses import dataclass
from datetime import date, datetime

from app.services.payroll_d5_event_sources import D5EventSource
from app.schemas.employment_termination_basis import EmploymentTerminationBasis


class D5MappingError(ValueError):
    pass


@dataclass(frozen=True)
class D5ProfessionalEvidence:
    employee_id: int
    tax_number: str
    professional_name: str
    profession_code: str
    position_name: str
    employment_category: str
    evidence_reference: str


@dataclass(frozen=True)
class D5MappedCandidate:
    source_event_id: int
    company_id: int
    employee_id: int
    contract_id: int
    event_kind: str
    event_date: date
    order_number: str
    order_date: date
    professional_name: str
    profession_code: str
    position_name: str
    tax_number: str
    employment_category: str
    evidence_reference: str
    termination_provision: str | None
    termination_source_reference: str | None


def _nonempty(value, label):
    if not isinstance(value, str) or not value.strip():
        raise D5MappingError(f"Documented {label} required")
    return value.strip()


def _positive(value, label):
    if type(value) is not int or value <= 0:
        raise D5MappingError(f"Valid {label} required")
    return value


def map_verified_d5_candidate(
    source: D5EventSource,
    professional: D5ProfessionalEvidence,
) -> D5MappedCandidate:
    """Validate an internal source candidate without inventing form fields."""
    if not isinstance(source, D5EventSource):
        raise D5MappingError("Typed D5 event source required")
    if not isinstance(professional, D5ProfessionalEvidence):
        raise D5MappingError("Typed professional evidence required")

    _positive(source.company_id, "company")
    _positive(source.employment_event_id, "source event")
    _positive(source.employment_contract_id, "contract")
    _positive(professional.employee_id, "employee")

    if source.event_type not in ("hire", "transfer", "termination"):
        raise D5MappingError("Unsupported D5 event type")

    if type(source.effective_date) is not date or type(source.order_date) is not date:
        raise D5MappingError("Real calendar dates required")
    if source.order_date > source.effective_date:
        raise D5MappingError("Order date cannot follow effective date")

    order = _nonempty(source.order_number, "HR order")
    tax_number = _nonempty(professional.tax_number, "employee tax identity")
    professional_name = _nonempty(
        professional.professional_name, "professional name"
    )
    profession_code = _nonempty(
        professional.profession_code, "profession classifier code"
    )
    position_name = _nonempty(professional.position_name, "position name")
    category = _nonempty(
        professional.employment_category, "employment category"
    )
    evidence_reference = _nonempty(
        professional.evidence_reference, "professional evidence"
    )

    provision = None
    termination_reference = None

    if source.event_type == "termination":
        try:
            basis = EmploymentTerminationBasis.model_validate(
                source.termination_basis
            )
        except Exception as exc:
            raise D5MappingError(
                "Valid documented termination legal basis required"
            ) from exc
        provision = basis.labour_code_provision
        termination_reference = basis.source_reference
    elif source.termination_basis is not None:
        raise D5MappingError(
            "Nontermination event cannot carry termination basis"
        )

    return D5MappedCandidate(
        source_event_id=source.employment_event_id,
        company_id=source.company_id,
        employee_id=professional.employee_id,
        contract_id=source.employment_contract_id,
        event_kind=source.event_type,
        event_date=source.effective_date,
        order_number=order,
        order_date=source.order_date,
        professional_name=professional_name,
        profession_code=profession_code,
        position_name=position_name,
        tax_number=tax_number,
        employment_category=category,
        evidence_reference=evidence_reference,
        termination_provision=provision,
        termination_source_reference=termination_reference,
    )
