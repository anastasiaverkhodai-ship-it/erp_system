"""Real PostgreSQL payroll-to-4DF fingerprint verification."""

from copy import deepcopy

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from test_employee_foundation import employee_engine
from test_payroll_regulatory_d1_completeness_postgresql import (
    prepare,
)

from app.models.payroll import PayrollPeriod
from app.services.payroll_regulatory_readiness_service import (
    payroll_regulatory_readiness,
)
from app.services.payroll_regulatory_4df_independent_provenance import (
    FourDFProvenanceError,
    source_fingerprint,
    group_fingerprint,
    build_independent_4df_provenance,
)


pytestmark = pytest.mark.asyncio


async def make_group(db, company_id, period_id):
    readiness = await payroll_regulatory_readiness(
        db,
        company_id=company_id,
        payroll_period_id=period_id,
        report_kind="4df",
    )

    sources = readiness["calculation_sources"]
    assert len(sources) == 1

    source = sources[0]

    period = await db.scalar(
        select(PayrollPeriod).where(
            PayrollPeriod.company_id == company_id,
            PayrollPeriod.id == period_id,
        )
    )
    assert period is not None

    taxes = {
        "personal_income_tax": "0.00",
        "military_levy": "0.00",
    }

    from decimal import Decimal

    amounts = {
        name: Decimal("0.00")
        for name in taxes
    }

    for component in source["components"]:
        name = component["component"]
        if name in amounts:
            amounts[name] += Decimal(str(component["amount"]))

    entry = {
        "source_id": source["payroll_calculation_id"],
        "source_sha256": source_fingerprint(
            company_id=company_id,
            payroll_period_id=period_id,
            source=source,
        ),
        "slice_number": 1,
        "slice_count": 1,
        "payment_date": period.end_date.isoformat(),
        "payment_evidence_reference":
            "TEST_ONLY_PAYMENT_NOT_POSTED",
        "tax_remittance_evidence_reference":
            "TEST_ONLY_TAX_NOT_REMITTED",
    }

    group = {
        "status": "REVIEW_ONLY_NOT_FOR_FILING",
        "statutory_approved": False,
        "company_id": company_id,
        "payroll_period_id": period_id,
        "employee_id": source["employee_id"],
        "reviewed_category_code": "TEST_ONLY",
        "income_month":
            period.start_date.replace(day=1).isoformat(),
        "payroll_calculation_ids": [
            source["payroll_calculation_id"]
        ],
        "payment_dates": [
            period.end_date.isoformat()
        ],
        "financial_evidence": [entry],
        "gross_accrued": str(source["gross_amount"]),
        "pit_accrued": str(
            amounts["personal_income_tax"]
        ),
        "levy_accrued": str(amounts["military_levy"]),
        "paid_amount": "0.00",
        "pit_remitted": "0.00",
        "levy_remitted": "0.00",
        "gross_paid_income_verified": False,
    }

    group["source_fingerprint"] = group_fingerprint(
        company_id=company_id,
        payroll_period_id=period_id,
        group=group,
    )

    return group


async def test_real_pg_fingerprint_and_tampering(
    employee_engine,
):
    async with AsyncSession(
        employee_engine, expire_on_commit=False
    ) as db:
        company_id, period_id = await prepare(db)

        group = await make_group(
            db, company_id, period_id
        )

        report = await build_independent_4df_provenance(
            db,
            company_id=company_id,
            payroll_period_id=period_id,
            financial_groups=[group],
        )

        assert report["source_fingerprint_verified"] is True
        assert report["posted_payment_verified"] is False
        assert report["gross_paid_income_verified"] is False
        assert report["official_generation"] == "BLOCKED"

        altered = deepcopy(group)
        altered["financial_evidence"][0][
            "source_sha256"
        ] = "f" * 64

        # Even a consistently recomputed group checksum must
        # not make a false PostgreSQL source fingerprint valid.
        altered["source_fingerprint"] = group_fingerprint(
            company_id=company_id,
            payroll_period_id=period_id,
            group=altered,
        )

        with pytest.raises(Exception):
            await build_independent_4df_provenance(
                db,
                company_id=company_id,
                payroll_period_id=period_id,
                financial_groups=[altered],
            )

        await db.rollback()


async def test_pg_foreign_company_fails_closed(
    employee_engine,
):
    async with AsyncSession(
        employee_engine, expire_on_commit=False
    ) as db:
        company_id, period_id = await prepare(db)
        group = await make_group(
            db, company_id, period_id
        )

        with pytest.raises(Exception):
            await build_independent_4df_provenance(
                db,
                company_id=company_id + 100000,
                payroll_period_id=period_id,
                financial_groups=[group],
            )

        await db.rollback()
