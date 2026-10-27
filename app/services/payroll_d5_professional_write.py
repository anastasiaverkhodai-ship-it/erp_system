
"""Verified, insert-only professional evidence persistence.

This is not an official statutory D5 writer. Evidence authenticity and
national profession classification require separate acceptance.
"""
from dataclasses import fields
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.payroll_d5_professional_evidence import (
    PayrollD5ProfessionalEvidence,
)
from app.services.payroll_d5_classifier_guard import (
    D5ClassifierError,
    verify_d5_classifier,
)
from app.services.payroll_d5_trusted_classifier import (
    import_approved_classifier,
)
from app.services.payroll_d5_event_sources import D5EventSource
from app.services.payroll_d5_database_position import (
    verify_database_d5_position,
)
from app.services.payroll_d5_position_provenance import (
    D5PositionEvidence,
)
from app.services.payroll_d5_professional_history import (
    D5ProfessionalHistoryRecord,
    D5ProfessionalHistoryError,
    validate_professional_history,
)


class D5ProfessionalWriteError(D5ProfessionalHistoryError):
    pass


def _payload(
    source, evidence, history, request_key, created_by,
    classifier_bytes,
):
    if not isinstance(request_key, str) or not request_key.strip():
        raise D5ProfessionalWriteError("Nonempty request key required")
    if len(request_key.strip()) > 200:
        raise D5ProfessionalWriteError("Request key too long")
    if type(created_by) is not int or created_by <= 0:
        raise D5ProfessionalWriteError("Valid actor required")

    try:
        validate_professional_history(
            source=source, position=evidence, record=history,
        )
    except D5ProfessionalHistoryError as exc:
        raise D5ProfessionalWriteError(str(exc)) from exc

    # Unapproved classifier bytes must never authorize a write.
    if not isinstance(classifier_bytes, bytes) or not classifier_bytes:
        raise D5ProfessionalWriteError(
            "Approved classifier source required"
        )

    try:
        registry_entries = import_approved_classifier(classifier_bytes)
        verify_d5_classifier(
            history=history,
            event_date=source.effective_date,
            entries=registry_entries,
        )
    except D5ClassifierError as exc:
        raise D5ProfessionalWriteError(
            f"Classifier verification refused: {exc}"
        ) from exc

    return dict(
        company_id=history.company_id,
        employee_id=history.employee_id,
        employment_contract_id=history.employment_contract_id,
        employment_event_id=source.employment_event_id,
        position_id=history.position_id,
        effective_from=history.effective_from,
        effective_to=history.effective_to,
        documented_on=history.documented_on,
        professional_name=history.professional_name,
        profession_code=history.profession_code,
        position_name=history.position_name,
        employment_category=history.employment_category,
        evidence_reference=history.evidence_reference,
        request_key=request_key.strip(),
        created_by=created_by,
    )


async def create_verified_d5_professional_evidence(
    db: AsyncSession,
    *,
    source: D5EventSource,
    evidence: D5PositionEvidence,
    history: D5ProfessionalHistoryRecord,
    request_key: str,
    created_by: int,
    classifier_bytes: bytes | None = None,
) -> PayrollD5ProfessionalEvidence:
    """Persist an evidence snapshot; reject conflicts and key reuse.

    Caller owns transaction and commit. Does not overwrite previous rows.
    """
    if not isinstance(source, D5EventSource):
        raise D5ProfessionalWriteError("Typed source required")
    if not isinstance(evidence, D5PositionEvidence):
        raise D5ProfessionalWriteError("Typed position required")
    if not isinstance(history, D5ProfessionalHistoryRecord):
        raise D5ProfessionalWriteError("Typed history required")

    values = _payload(
        source, evidence, history, request_key, created_by,
        classifier_bytes,
    )

    # Validate actual database identities and persisted HR event.
    await verify_database_d5_position(
        db, source=source, evidence=evidence,
    )

    # Serialize writes for this company; do not race on duplicate keys.
    from app.models.company import Company
    company = await db.scalar(
        select(Company).where(
            Company.id == source.company_id
        ).with_for_update()
    )
    if company is None:
        raise D5ProfessionalWriteError("Company not found")

    model = PayrollD5ProfessionalEvidence
    previous = await db.scalar(
        select(model).where(
            model.company_id == source.company_id,
            model.request_key == values["request_key"],
        )
    )
    if previous is not None:
        comparison = (
            "company_id", "employee_id",
            "employment_contract_id", "employment_event_id",
            "position_id", "effective_from", "effective_to",
            "documented_on", "professional_name", "profession_code",
            "position_name", "employment_category",
            "evidence_reference", "request_key", "created_by",
        )
        if any(
            getattr(previous, key) != values[key]
            for key in comparison
        ):
            raise D5ProfessionalWriteError(
                "Conflicting idempotency request"
            )
        return previous

    # A second record for the same event needs explicit correction
    # handling, not silent replacement or competing evidence.
    competing = await db.scalar(
        select(model.id).where(
            model.company_id == source.company_id,
            model.employment_event_id == source.employment_event_id,
        )
    )
    if competing is not None:
        raise D5ProfessionalWriteError(
            "Event already has professional evidence; "
            "correction workflow required"
        )

    record = model(**values)
    db.add(record)
    await db.flush()
    return record
