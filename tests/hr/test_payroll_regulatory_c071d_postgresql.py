"""C071-D: PostgreSQL-backed negative generator acceptance.

Real ORM report/row records are used. Readiness is substituted using
the existing integration fixture. No statutory mappings are approved.

All invalid evidence must fail before the normative mapping blocker.
The report must remain a draft.
"""

import json

import pytest

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from test_employee_foundation import employee_engine
from test_payroll_regulatory_generator_integration_postgresql import (
    initialize,
    assert_not_generated,
)

from app.models.payroll_regulatory_report import (
    PayrollRegulatoryReportRow,
)
from app.services.payroll_regulatory_report_service import (
    PayrollRegulatoryReportError,
    generate_payroll_regulatory_report,
)


pytestmark = pytest.mark.asyncio


def install_readiness(monkeypatch):
    import app.services.payroll_regulatory_readiness_service as readiness_module
    import app.services.payroll_regulatory_dated_4df_service as dated_module

    monkeypatch.setattr(
        readiness_module,
        "payroll_regulatory_readiness",
        dated_module.payroll_regulatory_readiness,
    )


async def get_rows(db, company_id, report_id):
    return list((
        await db.scalars(
            select(PayrollRegulatoryReportRow)
            .where(
                PayrollRegulatoryReportRow.company_id == company_id,
                PayrollRegulatoryReportRow.payroll_regulatory_report_id
                    == report_id,
            )
            .order_by(PayrollRegulatoryReportRow.row_no)
        )
    ).all())


async def expect_rejected(db, company_id, report_id):
    with pytest.raises(PayrollRegulatoryReportError) as error:
        await generate_payroll_regulatory_report(
            db,
            company_id=company_id,
            payroll_regulatory_report_id=report_id,
            generated_by=1,
        )

    message = str(error.value)

    # Corrupt financial evidence must not pass all the way to
    # the normal statutory approval blocker.
    assert "4DF financial evidence reconciled;" not in message
    assert "no independently approved normative mappings" not in message

    await assert_not_generated(
        db,
        company_id,
        report_id,
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "invalid_source_hash",
        "different_source_hash",
        "missing_payment_reference",
        "missing_tax_reference",
        "wrong_source_id",
        "wrong_income_month",
        "inconsistent_slice_count",
        "duplicate_slice_number",
        "wrong_company",
        "wrong_period",
    ],
)
async def test_corrupt_persisted_evidence_rejected(
    employee_engine,
    monkeypatch,
    mutation,
):
    async with AsyncSession(
        employee_engine,
        expire_on_commit=False,
    ) as db:
        company_id, report_id = await initialize(
            db, monkeypatch
        )

        install_readiness(monkeypatch)

        rows = await get_rows(
            db, company_id, report_id
        )

        assert len(rows) == 2

        row = rows[0]
        payload = json.loads(row.payload_json)

        if mutation == "invalid_source_hash":
            payload["source_sha256"] = "INVALID"

        elif mutation == "different_source_hash":
            payload["source_sha256"] = "c" * 64

        elif mutation == "missing_payment_reference":
            payload["payment_evidence_reference"] = ""

        elif mutation == "missing_tax_reference":
            payload["tax_remittance_evidence_reference"] = ""

        elif mutation == "wrong_source_id":
            payload["source_id"] = 99999999

        elif mutation == "wrong_income_month":
            payload["income_month"] = "2026-10-15"

        elif mutation == "inconsistent_slice_count":
            payload["slice_count"] = 3

        elif mutation == "duplicate_slice_number":
            payload["slice_number"] = 2

        elif mutation == "wrong_company":
            payload["company_id"] = company_id + 999999

        elif mutation == "wrong_period":
            payload["payroll_period_id"] = 99999999

        row.payload_json = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
        )

        await db.flush()

        await expect_rejected(
            db, company_id, report_id
        )

        await db.rollback()


async def test_missing_persisted_slice_rejected(
    employee_engine,
    monkeypatch,
):
    async with AsyncSession(
        employee_engine,
        expire_on_commit=False,
    ) as db:
        company_id, report_id = await initialize(
            db, monkeypatch
        )

        install_readiness(monkeypatch)

        rows = await get_rows(
            db, company_id, report_id
        )

        assert len(rows) == 2

        await db.delete(rows[1])
        await db.flush()

        await expect_rejected(
            db, company_id, report_id
        )

        await db.rollback()


async def test_mixed_report_rows_rejected(
    employee_engine,
    monkeypatch,
):
    async with AsyncSession(
        employee_engine,
        expire_on_commit=False,
    ) as db:
        company_id, report_id = await initialize(
            db, monkeypatch
        )

        install_readiness(monkeypatch)

        rows = await get_rows(
            db, company_id, report_id
        )

        assert len(rows) == 2

        # The generator must not silently ignore an unexpected
        # row type stored alongside valid financial evidence.
        rows[0].row_code = "UNEXPECTED_REVIEW_ROW"

        await db.flush()

        await expect_rejected(
            db, company_id, report_id
        )

        await db.rollback()


async def test_noncontiguous_persisted_rows_rejected(
    employee_engine,
    monkeypatch,
):
    async with AsyncSession(
        employee_engine,
        expire_on_commit=False,
    ) as db:
        company_id, report_id = await initialize(
            db, monkeypatch
        )

        install_readiness(monkeypatch)

        rows = await get_rows(
            db, company_id, report_id
        )

        assert len(rows) == 2

        rows[1].row_no = 99

        await db.flush()

        await expect_rejected(
            db, company_id, report_id
        )

        await db.rollback()
