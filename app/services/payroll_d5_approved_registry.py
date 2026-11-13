"""Approved civil profession registry and existing D5 evidence-write integration.

This approval does not authorize statutory form generation. Candidate history
is narrowed to an exact code/name identity: a code is not a unique profession.
"""
from datetime import date
import hashlib
from pathlib import Path

from app.services.payroll_d5_classifier_guard import D5ClassifierError, D5_SUPPORTED_FROM
from app.services.payroll_d5_trusted_classifier import import_approved_classifier
from app.services.payroll_d5_profession_history import HistoryResult, ProfessionPeriod

REGISTRY_PATH = Path(__file__).resolve().parents[1] / 'data/d5_civil_classifier_2026.json'


def load_approved_classifier_bytes():
    raw = REGISTRY_PATH.read_bytes()
    import_approved_classifier(raw)
    return raw


def approved_profession_history(*, profession_code, professional_name, event_date, raw=None):
    if type(event_date) is not date or event_date < D5_SUPPORTED_FROM:
        raise D5ClassifierError('Profession verification before 2026-01-01 is not supported')
    if not isinstance(profession_code, str) or not isinstance(professional_name, str):
        raise D5ClassifierError('Exact profession code and name required')
    if profession_code.startswith('0'):
        raise D5ClassifierError('Military section 0 is outside civil payroll support')
    raw = load_approved_classifier_bytes() if raw is None else raw
    entries = import_approved_classifier(raw)
    matched = [e for e in entries if e.profession_code == profession_code
               and e.professional_name == professional_name
               and e.effective_from <= event_date
               and (e.effective_to is None or event_date <= e.effective_to)]
    if len(matched) != 1:
        raise D5ClassifierError('Exactly one approved effective profession required')
    entry = matched[0]
    return HistoryResult(
        periods=(ProfessionPeriod(
            profession_code=entry.profession_code,
            title=entry.professional_name,
            valid_from=entry.effective_from,
            valid_to=entry.effective_to,
            source_id=entry.source_version,
            source_sha256=hashlib.sha256(raw).hexdigest(),
        ),),
        event_count=1,
    )


async def create_approved_d5_professional_evidence(
    db, *, source, evidence, history, request_key, created_by,
):
    """Use the shipped trust anchor while preserving all database guards.

    The caller owns the transaction. No approval is accepted from the caller.
    No HR source, historical evidence or immutable snapshot is rewritten.
    """
    from app.services.payroll_d5_hr_event_intake import persist_verified_d5_hr_event
    from app.services.payroll_d5_event_sources import D5EventSource
    from app.services.payroll_d5_professional_history import D5ProfessionalHistoryRecord
    if not isinstance(source, D5EventSource) or not isinstance(history, D5ProfessionalHistoryRecord):
        raise D5ClassifierError("Typed HR source and professional history required")
    raw = load_approved_classifier_bytes()
    candidate = approved_profession_history(
        profession_code=history.profession_code,
        professional_name=history.professional_name,
        event_date=source.effective_date,
        raw=raw,
    )
    return await persist_verified_d5_hr_event(
        db, source=source, evidence=evidence, history=history,
        candidate_history=candidate, classifier_bytes=raw,
        request_key=request_key, created_by=created_by,
    )
