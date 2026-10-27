from unittest.mock import AsyncMock, patch

import pytest

from app.services.payroll_dated_component_verified import (
    VerifiedDatedComponentError,
    preview_verified_dated_salary_tax,
)


@pytest.mark.asyncio
async def test_invalid_company_is_rejected_before_database_read():
    db = AsyncMock()

    with pytest.raises(
        VerifiedDatedComponentError,
        match="Invalid company identifier",
    ):
        await preview_verified_dated_salary_tax(
            db,
            company_id=-1,
            payroll_calculation_id=1,
            component=object(),
        )

    db.scalar.assert_not_called()
    db.scalars.assert_not_called()


@pytest.mark.asyncio
async def test_invalid_calculation_is_rejected():
    db = AsyncMock()

    with pytest.raises(
        VerifiedDatedComponentError,
        match="Invalid payroll calculation identifier",
    ):
        await preview_verified_dated_salary_tax(
            db,
            company_id=1,
            payroll_calculation_id=0,
            component=object(),
        )

    db.scalar.assert_not_called()
    db.scalars.assert_not_called()


@pytest.mark.asyncio
async def test_missing_source_is_rejected_without_posting():
    db = AsyncMock()

    with patch(
        "app.services.payroll_dated_component_verified."
        "load_verified_dated_salary_allocation",
        new_callable=AsyncMock,
        return_value=(),
    ):
        with pytest.raises(
            VerifiedDatedComponentError,
            match="Verified dated income is empty",
        ):
            await preview_verified_dated_salary_tax(
                db,
                company_id=1,
                payroll_calculation_id=2,
                component=object(),
            )


@pytest.mark.asyncio
async def test_special_profile_is_not_silently_taxed():
    from datetime import date
    from decimal import Decimal

    from app.services.payroll_dated_income_allocation import (
        DatedIncomeAllocation,
    )

    db = AsyncMock()

    allocation = DatedIncomeAllocation(
        source_line_id=1,
        effective_from=date(2026, 9, 1),
        effective_to=date(2026, 9, 14),
        amount=Decimal("1000.00"),
        source_rate_id=11,
        source_profile_id=21,
        source_base_rule_id=None,
    )

    with patch(
        "app.services.payroll_dated_component_verified."
        "load_verified_dated_salary_allocation",
        new_callable=AsyncMock,
        return_value=(allocation,),
    ):
        with pytest.raises(
            VerifiedDatedComponentError,
            match="Special tax profiles",
        ):
            await preview_verified_dated_salary_tax(
                db,
                company_id=1,
                payroll_calculation_id=2,
                component=object(),
            )

    db.scalars.assert_not_called()
