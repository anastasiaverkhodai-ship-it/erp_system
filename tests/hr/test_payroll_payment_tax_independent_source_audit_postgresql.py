"""Independent payroll source audit in disposable real PostgreSQL."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from test_employee_foundation import employee_engine
from test_payroll_payment_tax_secure_custody_postgresql import (
    setup,
)

from app.services.payroll_payment_tax_independent_source_audit import (
    audit_payment_tax_sources,
)
from app.services.payroll_payment_tax_allocation_candidate import (
    PaymentTaxAllocationError,
)

pytestmark = pytest.mark.asyncio


async def test_pg_actual_sources_remain_separately_classified(
    employee_engine,
    monkeypatch,
):
    async with AsyncSession(
        employee_engine,
        expire_on_commit=False,
    ) as db:
        (
            company, author, reviewer,
            evidence, integrity, custody,
        ) = await setup(db, monkeypatch)

        result = await audit_payment_tax_sources(
            db,
            company_id=company,
            evidence_id=evidence["evidence_id"],
        )

        assert result["read_only"] is True
        assert result["official_generation"] == "BLOCKED"
        assert (
            result["independent_actual_withholding"]
            == "NOT_VERIFIED"
        )
        assert (
            result["source_inventory"][
                "payment_specific_withholding_verified"
            ] is False
        )

        await db.rollback()


async def test_pg_cross_company_audit_is_denied(
    employee_engine,
    monkeypatch,
):
    async with AsyncSession(
        employee_engine,
        expire_on_commit=False,
    ) as db:
        (
            company, author, reviewer,
            evidence, integrity, custody,
        ) = await setup(db, monkeypatch)

        with pytest.raises(
            PaymentTaxAllocationError,
            match="not found for company",
        ):
            await audit_payment_tax_sources(
                db,
                company_id=company + 999999,
                evidence_id=evidence["evidence_id"],
            )

        await db.rollback()
