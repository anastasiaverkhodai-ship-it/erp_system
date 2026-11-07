"""Bind candidate D5 profession history to approved classifier bytes.

Candidate history alone has no legal authority. Independent approval
is verified by the existing trusted classifier importer.

This module never approves a normative source or generates a
statutory D5 report. Its write entry point preserves the existing
HR identity checks, transaction ownership and insert-only policy.
"""

from dataclasses import dataclass
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.payroll_d5_classifier_guard import (
    D5ClassifierError,
    verify_d5_classifier,
)
from app.services.payroll_d5_event_sources import D5EventSource
from app.services.payroll_d5_position_provenance import (
    D5PositionEvidence,
)
from app.services.payroll_d5_professional_history import (
    D5ProfessionalHistoryRecord,
)
from app.services.payroll_d5_profession_history import (
    HistoryResult,
    D5HistoryError,
    candidate_profession_on_date,
)
from app.services.payroll_d5_trusted_classifier import (
    import_approved_classifier,
)
from app.services.payroll_d5_professional_write import (
    create_verified_d5_professional_evidence,
)


class D5SourceHistoryBindingError(ValueError):
    pass


@dataclass(frozen=True)
class D5SourceHistoryBinding:
    profession_code: str
    professional_name: str
    event_date: date
    effective_from: date
    effective_to: date | None
    candidate_source_id: str
    candidate_source_sha256: str
    approved_source_reference: str
    approved_source_version: str
    normative_approved_by_candidate: bool = False
    official_generation_authorized: bool = False


def verify_d5_source_history_binding(
    *,
    source: D5EventSource,
    history: D5ProfessionalHistoryRecord,
    candidate_history: HistoryResult,
    classifier_bytes: bytes,
) -> D5SourceHistoryBinding:
    """Require exact effective-period concordance, never infer approval."""

    if not isinstance(source, D5EventSource):
        raise D5SourceHistoryBindingError(
            "Typed employment event source required"
        )

    if not isinstance(history, D5ProfessionalHistoryRecord):
        raise D5SourceHistoryBindingError(
            "Typed persisted professional history required"
        )

    if not isinstance(candidate_history, HistoryResult):
        raise D5SourceHistoryBindingError(
            "Typed candidate history required"
        )

    if (
        candidate_history.normative_approved is not False
        or candidate_history.official_generation_authorized is not False
    ):
        raise D5SourceHistoryBindingError(
            "Candidate history cannot declare statutory authorization"
        )

    if (
        type(candidate_history.event_count) is not int
        or candidate_history.event_count < 1
        or not isinstance(candidate_history.periods, tuple)
    ):
        raise D5SourceHistoryBindingError(
            "Invalid candidate history"
        )

    if (
        history.company_id != source.company_id
        or history.employment_contract_id
        != source.employment_contract_id
    ):
        raise D5SourceHistoryBindingError(
            "Event and historical contract identity mismatch"
        )

    if type(source.effective_date) is not date:
        raise D5SourceHistoryBindingError(
            "Valid event effective date required"
        )

    if (
        not isinstance(classifier_bytes, bytes)
        or not classifier_bytes
    ):
        raise D5SourceHistoryBindingError(
            "Approved classifier bytes required"
        )

    try:
        candidate = candidate_profession_on_date(
            candidate_history,
            code=history.profession_code,
            on_date=source.effective_date,
        )
    except D5HistoryError as exc:
        raise D5SourceHistoryBindingError(
            "Candidate profession lookup rejected"
        ) from exc

    if candidate is None:
        raise D5SourceHistoryBindingError(
            "No effective candidate profession"
        )

    if candidate.title != history.professional_name:
        raise D5SourceHistoryBindingError(
            "Candidate profession title mismatch"
        )

    if (
        not candidate.source_id.strip()
        or len(candidate.source_sha256) != 64
        or not all(
            c in "0123456789abcdef"
            for c in candidate.source_sha256
        )
    ):
        raise D5SourceHistoryBindingError(
            "Invalid candidate source provenance"
        )

    try:
        entries = import_approved_classifier(classifier_bytes)

        verified = verify_d5_classifier(
            history=history,
            event_date=source.effective_date,
            entries=entries,
        )
    except D5ClassifierError as exc:
        raise D5SourceHistoryBindingError(
            "Independent classifier verification rejected"
        ) from exc

    # A candidate period must reproduce the approved effective
    # interval exactly. Partial overlaps or inferred dates fail.
    matched = [
        item for item in entries
        if item.profession_code == candidate.profession_code
        and item.professional_name == candidate.title
        and item.effective_from <= source.effective_date
        and (
            item.effective_to is None
            or source.effective_date <= item.effective_to
        )
    ]

    if len(matched) != 1:
        raise D5SourceHistoryBindingError(
            "Exactly one approved candidate interval required"
        )

    approved = matched[0]

    if (
        candidate.valid_from != approved.effective_from
        or candidate.valid_to != approved.effective_to
    ):
        raise D5SourceHistoryBindingError(
            "Candidate and approved effective intervals differ"
        )

    return D5SourceHistoryBinding(
        profession_code=verified.profession_code,
        professional_name=verified.professional_name,
        event_date=verified.event_date,
        effective_from=approved.effective_from,
        effective_to=approved.effective_to,
        candidate_source_id=candidate.source_id,
        candidate_source_sha256=candidate.source_sha256,
        approved_source_reference=verified.source_reference,
        approved_source_version=verified.source_version,
    )


async def create_source_bound_d5_professional_evidence(
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
    """Validate normative concordance before existing verified write."""

    verify_d5_source_history_binding(
        source=source,
        history=history,
        candidate_history=candidate_history,
        classifier_bytes=classifier_bytes,
    )

    return await create_verified_d5_professional_evidence(
        db,
        source=source,
        evidence=evidence,
        history=history,
        candidate_history=candidate_history,
        classifier_bytes=classifier_bytes,
        request_key=request_key,
        created_by=created_by,
    )
