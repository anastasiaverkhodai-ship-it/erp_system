"""Real PostgreSQL scope and provenance integration."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from test_employee_foundation import employee_engine
from test_payroll_regulatory_d1_completeness_postgresql import (
    prepare,
)
from app.services.payroll_regulatory_readiness_service import (
    payroll_regulatory_readiness,
)
from app.services.payroll_regulatory_4df_d6_evidence_gate import (
    EvidenceGateError,
    payment_manifest,
    verify_posted_payment_manifest,
)


pytestmark = pytest.mark.asyncio


async def test_real_pg_requires_actual_payment_records(
    employee_engine,
):
    async with AsyncSession(
        employee_engine, expire_on_commit=False
    ) as db:
        company_id, period_id = await prepare(db)

        readiness = await payroll_regulatory_readiness(
            db,
            company_id=company_id,
            payroll_period_id=period_id,
            report_kind="4df",
        )
        assert readiness["calculation_sources"]

        with pytest.raises(EvidenceGateError):
            payment_manifest(
                company_id=company_id,
                payroll_period_id=period_id,
                allocations=[],
                calculation_sources=readiness[
                    "calculation_sources"
                ],
            )

        await db.rollback()


async def test_real_pg_foreign_source_rejected_before_posting(
    employee_engine,
):
    async with AsyncSession(
        employee_engine, expire_on_commit=False
    ) as db:
        company_id, period_id = await prepare(db)

        invalid = [{
            "payroll_calculation_id": 999999,
            "payment_date": None,
            "payroll_disbursement_ids": [999999],
            "tax_remittance_line_ids": [999998],
            "payment_evidence_reference":
                "payroll_disbursements:999999",
            "tax_remittance_evidence_reference":
                "payroll_tax_remittance_lines:999998",
            "paid_amount": "1.00",
            "pit_remitted": "0.00",
            "levy_remitted": "0.00",
        }]

        with pytest.raises(EvidenceGateError):
            await verify_posted_payment_manifest(
                db,
                company_id=company_id,
                payroll_period_id=period_id,
                reviewed_allocations=invalid,
            )

        await db.rollback()
