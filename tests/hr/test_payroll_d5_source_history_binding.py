"""Source-bound D5 classifier tests using isolated approvals only."""

from dataclasses import replace
from datetime import date
import hashlib
import json

import pytest

import app.services.payroll_d5_trusted_classifier as trusted

from app.services.payroll_d5_event_sources import D5EventSource
from app.services.payroll_d5_professional_history import (
    D5ProfessionalHistoryRecord,
)
from app.services.payroll_d5_profession_history import (
    ProfessionEvent,
    HistoryResult,
    D5HistoryError,
    build_candidate_history,
)
from app.services.payroll_d5_source_history_binding import (
    D5SourceHistoryBindingError,
    verify_d5_source_history_binding,
)


DAY = date(2026, 1, 1)
CODE = "2419.2"
TITLE = "Synthetic test profession"


def source():
    return D5EventSource(
        company_id=1,
        employment_event_id=10,
        employment_contract_id=20,
        event_type="hire",
        effective_date=DAY,
        order_number="TEST-ORDER",
        order_date=DAY,
        termination_basis=None,
    )


def history():
    return D5ProfessionalHistoryRecord(
        company_id=1,
        employee_id=30,
        employment_contract_id=20,
        position_id=40,
        effective_from=DAY,
        effective_to=None,
        documented_on=DAY,
        evidence_reference="TEST-EVIDENCE",
        professional_name=TITLE,
        profession_code=CODE,
        position_name="Synthetic position",
        employment_category="primary",
    )


def event(*, title=TITLE, candidates=(DAY,)):
    return ProfessionEvent(
        profession_code=CODE,
        operation="add",
        effective_date=DAY,
        candidate_dates=candidates,
        title=title,
        source_id="test-source",
        source_sha256="a" * 64,
        source_reference="Synthetic evidence",
    )


def approved_bytes(monkeypatch, *, start=DAY, end=None,
                   title=TITLE):
    raw = json.dumps({
        "source_reference": "synthetic-approved-test-only",
        "source_version": "TEST",
        "entries": [{
            "profession_code": CODE,
            "professional_name": title,
            "effective_from": start.isoformat(),
            "effective_to": (
                end.isoformat() if end is not None else None
            ),
        }],
    }, sort_keys=True).encode("utf-8")

    monkeypatch.setattr(
        trusted,
        "APPROVED_SOURCES",
        (trusted.ApprovedClassifierSource(
            sha256=hashlib.sha256(raw).hexdigest(),
            source_reference="synthetic-approved-test-only",
            source_version="TEST",
        ),),
    )
    return raw


def verify(monkeypatch, candidate=None, **kwargs):
    raw = approved_bytes(monkeypatch, **kwargs)
    return verify_d5_source_history_binding(
        source=source(),
        history=history(),
        candidate_history=(
            candidate if candidate is not None
            else build_candidate_history([event()])
        ),
        classifier_bytes=raw,
    )


def test_exact_source_bound_match(monkeypatch):
    result = verify(monkeypatch)
    assert result.profession_code == CODE
    assert result.professional_name == TITLE
    assert result.effective_from == DAY
    assert result.effective_to is None
    assert result.official_generation_authorized is False
    assert result.normative_approved_by_candidate is False


def test_candidate_title_mismatch_rejected(monkeypatch):
    candidate = build_candidate_history([
        event(title="Forged title")
    ])
    with pytest.raises(
        D5SourceHistoryBindingError,
        match="title mismatch",
    ):
        verify(monkeypatch, candidate)


def test_candidate_starts_later_than_approved_rejected(monkeypatch):
    with pytest.raises(
        D5SourceHistoryBindingError,
        match="intervals differ",
    ):
        verify(
            monkeypatch,
            start=date(2025, 12, 31),
        )


def test_candidate_end_differs_from_approved_rejected(monkeypatch):
    with pytest.raises(
        D5SourceHistoryBindingError,
        match="intervals differ",
    ):
        verify(
            monkeypatch,
            end=date(2026, 1, 31),
        )


def test_missing_candidate_rejected(monkeypatch):
    raw = approved_bytes(monkeypatch)
    empty = HistoryResult(
        periods=(),
        event_count=1,
    )
    with pytest.raises(
        D5SourceHistoryBindingError,
        match="No effective candidate",
    ):
        verify_d5_source_history_binding(
            source=source(),
            history=history(),
            candidate_history=empty,
            classifier_bytes=raw,
        )


def test_unapproved_registry_rejected(monkeypatch):
    raw = approved_bytes(monkeypatch)
    monkeypatch.setattr(trusted, "APPROVED_SOURCES", ())

    with pytest.raises(
        D5SourceHistoryBindingError,
        match="Independent classifier",
    ):
        verify_d5_source_history_binding(
            source=source(),
            history=history(),
            candidate_history=build_candidate_history([event()]),
            classifier_bytes=raw,
        )


def test_forged_registry_bytes_rejected(monkeypatch):
    raw = approved_bytes(monkeypatch)
    with pytest.raises(
        D5SourceHistoryBindingError,
        match="Independent classifier",
    ):
        verify_d5_source_history_binding(
            source=source(),
            history=history(),
            candidate_history=build_candidate_history([event()]),
            classifier_bytes=raw + b" ",
        )


def test_disputed_normative_date_remains_blocked():
    with pytest.raises(
        D5HistoryError,
        match="Unresolved normative",
    ):
        build_candidate_history([
            event(candidates=(
                date(2026, 1, 1),
                date(2026, 2, 1),
            ))
        ])


def test_candidate_cannot_claim_legal_approval(monkeypatch):
    raw = approved_bytes(monkeypatch)
    candidate = replace(
        build_candidate_history([event()]),
        normative_approved=True,
    )
    with pytest.raises(
        D5SourceHistoryBindingError,
        match="cannot declare statutory",
    ):
        verify_d5_source_history_binding(
            source=source(),
            history=history(),
            candidate_history=candidate,
            classifier_bytes=raw,
        )


def test_contract_identity_mismatch_rejected(monkeypatch):
    raw = approved_bytes(monkeypatch)
    with pytest.raises(
        D5SourceHistoryBindingError,
        match="identity mismatch",
    ):
        verify_d5_source_history_binding(
            source=source(),
            history=replace(
                history(), employment_contract_id=999
            ),
            candidate_history=build_candidate_history([event()]),
            classifier_bytes=raw,
        )
