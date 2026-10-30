from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.payroll_taxpayer_identity_review_service import (
    TaxpayerIdentityReviewError,
    decide_identity_review,
    require_reviewed_identity,
)


def database(row):
    db = MagicMock()
    db.scalar = AsyncMock(return_value=row)
    db.flush = AsyncMock()
    return db


@pytest.mark.asyncio
async def test_independent_review_decision():
    row = SimpleNamespace(
        id=1,
        company_id=2,
        employee_id=3,
        created_by=4,
        review_status="pending",
        tax_number="1234567890",
        reviewed_by=None,
        reviewed_at=None,
    )

    db = database(row)

    result = await decide_identity_review(
        db,
        company_id=2,
        review_id=1,
        reviewed_by=5,
        approved=True,
    )

    assert result.review_status == "verified"
    assert result.reviewed_by == 5
    assert result.reviewed_at is not None


@pytest.mark.asyncio
async def test_verified_identity_date_window():
    row = SimpleNamespace(
        review_status="verified",
        identity_kind="rnokpp",
        tax_number="1234567890",
        valid_from=date(2026, 1, 1),
        valid_to=date(2026, 12, 31),
    )

    db = database(row)

    result = await require_reviewed_identity(
        db,
        company_id=2,
        employee_id=3,
        review_id=1,
        effective_on=date(2026, 10, 1),
        expected_tax_number="1234567890",
    )

    assert result is row


@pytest.mark.asyncio
async def test_expired_identity_rejected():
    row = SimpleNamespace(
        review_status="verified",
        identity_kind="rnokpp",
        tax_number="1234567890",
        valid_from=date(2025, 1, 1),
        valid_to=date(2025, 12, 31),
    )

    with pytest.raises(
        TaxpayerIdentityReviewError,
        match="not valid",
    ):
        await require_reviewed_identity(
            database(row),
            company_id=2,
            employee_id=3,
            review_id=1,
            effective_on=date(2026, 10, 1),
            expected_tax_number="1234567890",
        )
