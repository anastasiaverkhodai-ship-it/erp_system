"""C071-E real ORM verification of persisted payroll sources."""

import copy

import pytest

from sqlalchemy.ext.asyncio import AsyncSession

from test_employee_foundation import employee_engine
from test_payroll_regulatory_readiness_postgresql import seed

from app.services.payroll_regulatory_readiness_service import (
    payroll_regulatory_readiness,
)
from app.services.payroll_regulatory_persisted_source_verifier import (
    PersistedSourceIntegrityError,
    verify_persisted_payroll_sources,
)


pytestmark = pytest.mark.asyncio


async def prepare_real_sources(db):
    company, user, contract, calc, bank, beneficiary, claim = (
        await seed(db)
    )

    readiness = await payroll_regulatory_readiness(
        db,
        company_id=company.id,
        payroll_period_id=calc.payroll_period_id,
        report_kind="4df",
    )

    return company.id, calc.payroll_period_id, readiness



def test_rate_precision_preserved_independently_of_money():
    from decimal import Decimal

    from app.services.payroll_regulatory_persisted_source_verifier import (
        money,
        money_rate,
        PersistedSourceIntegrityError,
    )

    rate = Decimal("0.12345678")

    assert money_rate(rate) == rate
    assert money_rate("0.12345678") == rate

    with pytest.raises(PersistedSourceIntegrityError):
        money(rate)


def test_invalid_rate_rejected():
    from app.services.payroll_regulatory_persisted_source_verifier import (
        money_rate,
        PersistedSourceIntegrityError,
    )

    for value in ("NaN", "Infinity", "not-a-rate"):
        with pytest.raises(PersistedSourceIntegrityError):
            money_rate(value)


async def test_real_persisted_sources_verified(employee_engine):
    async with AsyncSession(
        employee_engine,
        expire_on_commit=False,
    ) as db:
        company_id, period_id, readiness = (
            await prepare_real_sources(db)
        )

        result = await verify_persisted_payroll_sources(
            db,
            company_id=company_id,
            payroll_period_id=period_id,
            readiness=readiness,
        )

        assert result["verified"] is True
        assert result["statutory_approved"] is False
        assert result["source_count"] == len(
            readiness["calculation_sources"]
        )
        assert result["component_count"] > 0

        await db.rollback()


@pytest.mark.parametrize(
    "field",
    [
        "employee_id",
        "employment_contract_id",
        "revision",
        "tax_number",
        "gross_amount",
        "net_amount",
        "employee_withholding_amount",
        "employer_contribution_amount",
        "currency_code",
    ],
)
async def test_tampered_readiness_source_rejected(
    employee_engine,
    field,
):
    async with AsyncSession(
        employee_engine,
        expire_on_commit=False,
    ) as db:
        company_id, period_id, readiness = (
            await prepare_real_sources(db)
        )

        corrupted = copy.deepcopy(readiness)
        corrupted["calculation_sources"][0][field] = "TAMPERED"

        with pytest.raises(PersistedSourceIntegrityError):
            await verify_persisted_payroll_sources(
                db,
                company_id=company_id,
                payroll_period_id=period_id,
                readiness=corrupted,
            )

        await db.rollback()


@pytest.mark.parametrize(
    "field",
    [
        "source_line_id",
        "component",
        "base_amount",
        "rate",
        "amount",
        "currency_code",
    ],
)
async def test_tampered_component_rejected(
    employee_engine,
    field,
):
    async with AsyncSession(
        employee_engine,
        expire_on_commit=False,
    ) as db:
        company_id, period_id, readiness = (
            await prepare_real_sources(db)
        )

        corrupted = copy.deepcopy(readiness)
        components = corrupted["calculation_sources"][0]["components"]

        assert components

        components[0][field] = "TAMPERED"

        with pytest.raises(PersistedSourceIntegrityError):
            await verify_persisted_payroll_sources(
                db,
                company_id=company_id,
                payroll_period_id=period_id,
                readiness=corrupted,
            )

        await db.rollback()


async def test_missing_current_calculation_rejected(employee_engine):
    async with AsyncSession(
        employee_engine,
        expire_on_commit=False,
    ) as db:
        company_id, period_id, readiness = (
            await prepare_real_sources(db)
        )

        corrupted = copy.deepcopy(readiness)
        corrupted["calculation_sources"] = []

        with pytest.raises(PersistedSourceIntegrityError):
            await verify_persisted_payroll_sources(
                db,
                company_id=company_id,
                payroll_period_id=period_id,
                readiness=corrupted,
            )

        await db.rollback()


async def test_duplicate_current_calculation_rejected(employee_engine):
    async with AsyncSession(
        employee_engine,
        expire_on_commit=False,
    ) as db:
        company_id, period_id, readiness = (
            await prepare_real_sources(db)
        )

        corrupted = copy.deepcopy(readiness)
        corrupted["calculation_sources"].append(
            copy.deepcopy(corrupted["calculation_sources"][0])
        )

        with pytest.raises(PersistedSourceIntegrityError):
            await verify_persisted_payroll_sources(
                db,
                company_id=company_id,
                payroll_period_id=period_id,
                readiness=corrupted,
            )

        await db.rollback()


async def test_cross_company_scope_rejected(employee_engine):
    async with AsyncSession(
        employee_engine,
        expire_on_commit=False,
    ) as db:
        company_id, period_id, readiness = (
            await prepare_real_sources(db)
        )

        with pytest.raises(PersistedSourceIntegrityError):
            await verify_persisted_payroll_sources(
                db,
                company_id=company_id + 100000,
                payroll_period_id=period_id,
                readiness=readiness,
            )

        await db.rollback()
