from datetime import date

import pytest

from app.services.payroll_d5_database_field_projection import (
    D5DatabaseProjectionError,
    _canonical_digest,
    _termination_reference,
    project_persisted_d5_event,
)


def test_evidence_digest_deterministic():
    payload = {
        "event": 1,
        "day": date(2026, 10, 15),
    }
    assert _canonical_digest(payload) == (
        _canonical_digest(payload)
    )


def test_evidence_digest_detects_changed_fact():
    assert _canonical_digest({"event": 1}) != (
        _canonical_digest({"event": 2})
    )


def test_termination_reference_requires_legal_basis():
    class Source:
        event_type = "termination"
        termination_basis = None

    with pytest.raises(
        D5DatabaseProjectionError
    ):
        _termination_reference(Source())


@pytest.mark.asyncio
async def test_invalid_company_rejected_before_db():
    class ForbiddenDB:
        async def scalar(self, *args):
            raise AssertionError("DB accessed")

    with pytest.raises(
        D5DatabaseProjectionError
    ):
        await project_persisted_d5_event(
            ForbiddenDB(),
            company_id=0,
            employment_event_id=1,
        )


@pytest.mark.asyncio
async def test_invalid_event_id_rejected_before_db():
    class ForbiddenDB:
        async def scalar(self, *args):
            raise AssertionError("DB accessed")

    with pytest.raises(
        D5DatabaseProjectionError
    ):
        await project_persisted_d5_event(
            ForbiddenDB(),
            company_id=1,
            employment_event_id=True,
        )
