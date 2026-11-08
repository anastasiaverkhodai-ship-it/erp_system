"""Actual source-bound claim guard integration E2E."""

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


@pytest.mark.asyncio
async def test_pg_claim_guard_is_bound_to_real_evidence(
    employee_engine,
    monkeypatch,
):
    async with AsyncSession(
        employee_engine,
        expire_on_commit=False,
    ) as db:
        (
            company,
            author,
            reviewer,
            evidence,
            integrity,
            custody,
        ) = await setup(db, monkeypatch)

        result = await audit_payment_tax_sources(
            db,
            company_id=company,
            evidence_id=evidence["evidence_id"],
        )

        binding = result["payment_specific_claim_binding"]

        assert binding["claim_count"] == 1
        assert binding["company_id"] == company
        assert binding["official_generation"] == "BLOCKED"
        assert (
            binding["payment_specific_withholding"]
            == "NOT_VERIFIED"
        )
        assert (
            binding["independent_source_authenticity"]
            == "NOT_VERIFIED"
        )
        assert (
            binding["gross_paid_attribution"]
            == "NOT_APPROVED"
        )

        claim = binding["claims"][0]

        assert claim["payment_id"] == (
            result["payroll_disbursement_id"]
        )
        assert claim["source_id"] == (
            evidence["evidence_id"]
        )
        assert claim["component"] in (
            "personal_income_tax",
            "military_levy",
        )
        assert claim["actual_withholding"] == "NOT_VERIFIED"
        assert result["independent_actual_withholding"] == (
            "NOT_VERIFIED"
        )
        assert result["official_generation"] == "BLOCKED"
        assert result["read_only"] is True

        # The audit must reject evidence from another company.
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
