"""Fail-closed D5 hire intake input validation."""

import pytest

from app.services.payroll_d5_hire_intake import (
    D5HireIntakeError,
    persist_verified_d5_hire_event,
)


@pytest.mark.asyncio
async def test_missing_session_refused():
    with pytest.raises(
        D5HireIntakeError,
        match="Async database session",
    ):
        await persist_verified_d5_hire_event(
            None,
            source=None,
            evidence=None,
            history=None,
            candidate_history=None,
            classifier_bytes=b"",
            request_key="test",
            created_by=1,
        )


@pytest.mark.asyncio
async def test_untyped_source_refused():
    from unittest.mock import create_autospec
    from sqlalchemy.ext.asyncio import AsyncSession

    db = create_autospec(
        AsyncSession,
        instance=True,
    )

    with pytest.raises(
        D5HireIntakeError,
        match="Typed employment source",
    ):
        await persist_verified_d5_hire_event(
            db,
            source=None,
            evidence=None,
            history=None,
            candidate_history=None,
            classifier_bytes=b"",
            request_key="test",
            created_by=1,
        )
