import pytest
from unittest.mock import AsyncMock, MagicMock

from app.services.payroll_severance_financial_guard import (
    require_severance_financial_classification,
)


@pytest.mark.asyncio
async def test_blocks_severance_without_dated_classification():
    db = MagicMock()
    db.scalar = AsyncMock(return_value=42)

    with pytest.raises(
        ValueError,
        match="Severance statutory classification",
    ):
        await require_severance_financial_classification(
            db,
            company_id=1,
            payroll_calculation_id=10,
            error_type=ValueError,
        )

    db.scalar.assert_awaited_once()


@pytest.mark.asyncio
async def test_allows_non_severance_calculation():
    db = MagicMock()
    db.scalar = AsyncMock(return_value=None)

    await require_severance_financial_classification(
        db,
        company_id=1,
        payroll_calculation_id=10,
        error_type=ValueError,
    )

    db.scalar.assert_awaited_once()
