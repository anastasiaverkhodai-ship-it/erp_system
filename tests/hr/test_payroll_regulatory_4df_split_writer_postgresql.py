"""Isolated PostgreSQL production-writer split acceptance.

Real ORM report rows and production writer.
Mocked readiness, mapping approval and posted-payment verification
are explicitly limited to this integration test. Actual posted-payment
verification is covered separately by payment-bridge PostgreSQL tests.
No official mapping or statutory filing authorization is asserted.
"""

from copy import deepcopy
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.payroll_regulatory_report import (
    PayrollRegulatoryReport,
    PayrollRegulatoryReportRow,
)
from app.services import payroll_regulatory_form_row_writer as writer
from app.services.payroll_regulatory_report_service import (
    PayrollRegulatoryReportError,
)

pytestmark = pytest.mark.asyncio


@pytest.fixture
def employee_engine():
    import os
    from sqlalchemy.engine import make_url
    from sqlalchemy.ext.asyncio import create_async_engine

    raw = os.environ.get("DATABASE_URL")
    if not raw:
        pytest.fail("Isolated DATABASE_URL required")

    url = make_url(raw)
    if (
        not url.drivername.startswith("postgresql")
        or not (url.database or "").startswith("erp_r3_copy_")
    ):
        pytest.fail("Refusing non-isolated PostgreSQL database")

    if url.drivername != "postgresql+asyncpg":
        url = url.set(drivername="postgresql+asyncpg")

    return create_async_engine(url, pool_pre_ping=True)



def source_for(calculation, employee_id, contract_id):
    return {
        "payroll_calculation_id": calculation.id,
        "employee_id": employee_id,
        "employment_contract_id": contract_id,
        "issues": [],
        "gross_amount": Decimal("1000.00"),
        "components": [
            {
                "source_line_id": 1,
                "component": "personal_income_tax",
                "amount": Decimal("180.00"),
            },
            {
                "source_line_id": 2,
                "component": "military_levy",
                "amount": Decimal("50.00"),
            },
        ],
    }


async def test_real_form_writer_two_slices_and_idempotency(
    employee_engine,
    monkeypatch,
):
    # Reuse the already-proven real PostgreSQL payroll seed.
    from test_payroll_regulatory_reporting_postgresql import (
        _seed_payroll,
    )

    async with AsyncSession(
        employee_engine,
        expire_on_commit=False,
    ) as db:
        (
            company,
            actor,
            employee,
            contract,
            period,
            payroll_input,
            calculation,
        ) = await _seed_payroll(db)

        report = PayrollRegulatoryReport(
            company_id=company.id,
            payroll_period_id=period.id,
            report_kind="4df",
            revision=1,
            status="draft",
            created_by=actor.id,
        )
        db.add(report)
        await db.flush()

        source = source_for(
            calculation,
            employee.id,
            contract.id,
        )

        readiness = {
            "company_id": company.id,
            "payroll_period_id": period.id,
            "report_kind": "4df",
            "blocking_issues": [
                "DATED_STATUTORY_FORM_MAPPING_MISSING",
            ],
            "calculation_sources": [source],
        }

        mapping = SimpleNamespace(
            official_reference="TEST_ONLY_UNAPPROVED_MAPPING",
            source_sha256="a" * 64,
        )

        expected_company_id = company.id
        expected_period_id = period.id

        async def fake_readiness(
            db_arg,
            *,
            company_id,
            payroll_period_id,
            report_kind,
        ):
            assert db_arg is db
            assert company_id == expected_company_id
            assert payroll_period_id == expected_period_id
            assert report_kind == "4df"
            return deepcopy(readiness)

        # These references represent test-only review evidence.
        # No official mapping is approved by this fixture.
        monkeypatch.setattr(
            writer,
            "approved_mappings_for",
            lambda kind, start, end: {7: mapping},
        )
        monkeypatch.setattr(
            writer,
            "payroll_regulatory_readiness",
            fake_readiness,
        )

        # The production function imports this verifier internally.
        # Separate real PostgreSQL bridge tests run in the same delivery.
        from app.services import (
            payroll_regulatory_payment_evidence as payment_module,
        )

        expected_employee_id = employee.id

        async def fake_payment_verifier(
            db_arg,
            *,
            company_id,
            reviewed_allocations,
        ):
            assert db_arg is db
            assert company_id == expected_company_id

            payments = set()
            taxes = set()

            for allocation in reviewed_allocations:
                assert allocation["_verified_employee_id"] == expected_employee_id

                payment_ids = allocation["payroll_disbursement_ids"]
                tax_ids = allocation["tax_remittance_line_ids"]

                assert not payments.intersection(payment_ids)
                assert not taxes.intersection(tax_ids)

                payments.update(payment_ids)
                taxes.update(tax_ids)

                allocation["payment_evidence_reference"] = (
                    "test-payment:" +
                    ",".join(map(str, payment_ids))
                )
                allocation["tax_remittance_evidence_reference"] = (
                    "test-tax:" +
                    ",".join(map(str, tax_ids))
                )

        monkeypatch.setattr(
            payment_module,
            "verify_4df_posted_payment_evidence",
            fake_payment_verifier,
        )

        allocations = []

        for number, gross, pit, levy in (
            (1, "600.00", "108.00", "30.00"),
            (2, "400.00", "72.00", "20.00"),
        ):
            allocations.append({
                "payroll_calculation_id": calculation.id,
                "employee_id": employee.id,
                "slice_number": number,
                "slice_count": 2,
                "normative_mapping_id": 7,
                "official_reference": mapping.official_reference,
                "normative_source_sha256": mapping.source_sha256,
                "review_reference": f"TEST-REVIEW-{number}",
                "category_code": f"TEST-CATEGORY-{number}",
                "income_month": date(2026, 10, 1),
                "payment_date": date(2026, 10, 20 + number),
                "accrued_amount": gross,
                "pit_accrued": pit,
                "levy_accrued": levy,
                "paid_amount": gross,
                "pit_remitted": pit,
                "levy_remitted": levy,
                "payroll_disbursement_ids": [100000 + number],
                "tax_remittance_line_ids": [200000 + number],
            })

        rows = await writer.persist_reviewed_form_rows(
            db,
            company_id=company.id,
            payroll_regulatory_report_id=report.id,
            reviewed_allocations=allocations,
        )

        assert len(rows) == 2
        assert [row.row_no for row in rows] == [1, 2]
        assert all(row.id is not None for row in rows)

        ids = [row.id for row in rows]
        originals = [row.payload_json for row in rows]

        # Preserve scalar identifiers before expiring ORM entities.
        # Accessing an expired attribute from AsyncSession may trigger
        # implicit IO outside greenlet_spawn (MissingGreenlet).
        saved_company_id = company.id
        saved_report_id = report.id

        db.expire_all()

        persisted = list((
            await db.scalars(
                select(PayrollRegulatoryReportRow)
                .where(
                    PayrollRegulatoryReportRow.company_id
                    == saved_company_id,
                    PayrollRegulatoryReportRow.payroll_regulatory_report_id
                    == saved_report_id,
                )
                .order_by(PayrollRegulatoryReportRow.row_no)
            )
        ).all())

        assert [row.id for row in persisted] == ids
        assert [row.payload_json for row in persisted] == originals

        print("REAL FORM WRITER TWO-SLICE PERSISTENCE = PASS")

        repeated = await writer.persist_reviewed_form_rows(
            db,
            company_id=saved_company_id,
            payroll_regulatory_report_id=saved_report_id,
            reviewed_allocations=allocations,
        )

        assert [row.id for row in repeated] == ids

        print("REAL FORM WRITER IDEMPOTENCY = PASS")

        tampered = deepcopy(allocations)
        tampered[0]["review_reference"] = "ALTERED-REVIEW"

        with pytest.raises(PayrollRegulatoryReportError):
            await writer.persist_reviewed_form_rows(
                db,
                company_id=saved_company_id,
                payroll_regulatory_report_id=saved_report_id,
                reviewed_allocations=tampered,
            )

        after = list((
            await db.scalars(
                select(PayrollRegulatoryReportRow)
                .where(
                    PayrollRegulatoryReportRow.company_id
                    == saved_company_id,
                    PayrollRegulatoryReportRow.payroll_regulatory_report_id
                    == saved_report_id,
                )
                .order_by(PayrollRegulatoryReportRow.row_no)
            )
        ).all())

        assert [row.payload_json for row in after] == originals

        await db.refresh(report)
        assert report.status == "draft"
        assert report.generated_at is None

        print("REAL FORM WRITER SNAPSHOT REWRITE BLOCKED = PASS")
        print("REAL FORM WRITER DRAFT STATUS = PASS")

        await db.rollback()
