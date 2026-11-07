"""D5 multi-event intake rejects invalid source kinds and identity."""

from dataclasses import replace
from datetime import date
from unittest.mock import create_autospec

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.payroll_d5_event_sources import D5EventSource
from app.services.payroll_d5_hr_event_intake import (
    D5HREventIntakeError,
    SUPPORTED_EVENTS,
    persist_verified_d5_hr_event,
)


DAY = date(2026, 1, 1)


def source(kind):
    return D5EventSource(
        company_id=1,
        employment_event_id=10,
        employment_contract_id=20,
        event_type=kind,
        effective_date=DAY,
        order_number="TEST-ORDER",
        order_date=DAY,
        termination_basis=None,
    )


def session():
    return create_autospec(
        AsyncSession,
        instance=True,
    )


def arguments(source_value):
    return dict(
        source=source_value,
        evidence=None,
        history=None,
        candidate_history=None,
        classifier_bytes=b"",
        request_key="test",
        created_by=1,
    )


def test_supported_event_contract():
    assert SUPPORTED_EVENTS == {
        "hire",
        "transfer",
        "termination",
    }
    assert "reversal" not in SUPPORTED_EVENTS


@pytest.mark.asyncio
async def test_session_required():
    with pytest.raises(
        D5HREventIntakeError,
        match="Async database session",
    ):
        await persist_verified_d5_hr_event(
            None,
            **arguments(source("hire")),
        )


@pytest.mark.asyncio
async def test_source_type_required():
    with pytest.raises(
        D5HREventIntakeError,
        match="Typed HR event",
    ):
        await persist_verified_d5_hr_event(
            session(),
            **arguments(None),
        )


@pytest.mark.asyncio
async def test_reversal_rejected():
    with pytest.raises(
        D5HREventIntakeError,
        match="Unsupported HR event",
    ):
        await persist_verified_d5_hr_event(
            session(),
            **arguments(source("reversal")),
        )


@pytest.mark.asyncio
async def test_unknown_type_rejected():
    with pytest.raises(
        D5HREventIntakeError,
        match="Unsupported HR event",
    ):
        await persist_verified_d5_hr_event(
            session(),
            **arguments(source("promotion")),
        )


@pytest.mark.asyncio
async def test_untyped_position_rejected():
    with pytest.raises(
        D5HREventIntakeError,
        match="Typed position evidence",
    ):
        await persist_verified_d5_hr_event(
            session(),
            **arguments(source("transfer")),
        )


@pytest.mark.asyncio
async def test_untyped_source_history_rejected():
    args = arguments(source("termination"))
    assert args["candidate_history"] is None

    with pytest.raises(
        D5HREventIntakeError,
        match="Typed position evidence",
    ):
        await persist_verified_d5_hr_event(
            session(),
            **args,
        )
