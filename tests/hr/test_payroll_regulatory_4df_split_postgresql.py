"""PostgreSQL persistence proof for internal split 4DF evidence.

Uses a transaction-local PostgreSQL temporary table. This verifies
storage and re-reading of the real split allocator's output, not
the regulatory report writer or authorization to file an official form.
"""
import asyncio
import hashlib
import json
import os
from datetime import date
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from app.services.payroll_regulatory_4df_split_allocation import (
    build_split_4df_draft_rows,
    FourDFSplitAllocationError,
)
from app.services.payroll_regulatory_generated_integrity import (
    validate_mapped_report_rows,
    RegulatoryGeneratedIntegrityError,
)
from app.services.payroll_regulatory_source_snapshot_service import (
    canonical_json,
)


def scenario():
    report = SimpleNamespace(
        company_id=11,
        payroll_period_id=22,
        report_kind="4df",
    )

    source = {
        "payroll_calculation_id": 33,
        "employee_id": 44,
        "employment_contract_id": 55,
        "gross_amount": "1500.00",
        "issues": [],
        "components": [
            {
                "source_line_id": 101,
                "component": "personal_income_tax",
                "amount": "270.00",
            },
            {
                "source_line_id": 102,
                "component": "military_levy",
                "amount": "75.00",
            },
        ],
    }

    readiness = {
        "company_id": 11,
        "payroll_period_id": 22,
        "report_kind": "4df",
        "blocking_issues": [
            "DATED_STATUTORY_FORM_MAPPING_MISSING"
        ],
        "calculation_sources": [source],
    }

    mapping = SimpleNamespace(
        official_reference="TEST_ONLY_NOT_APPROVED_FOR_FILING",
        source_sha256="a" * 64,
    )

    evidence = []

    for index, gross, pit, levy, payment_date in (
        (1, "1000.00", "180.00", "50.00", date(2026, 10, 20)),
        (2, "500.00", "90.00", "25.00", date(2026, 10, 31)),
    ):
        evidence.append({
            "payroll_calculation_id": 33,
            "slice_number": index,
            "slice_count": 2,
            "normative_mapping_id": 7,
            "official_reference": mapping.official_reference,
            "normative_source_sha256": mapping.source_sha256,
            "review_reference": f"TEST-REVIEW-{index}",
            "category_code": f"TEST-CATEGORY-{index}",
            "income_month": date(2026, 10, 1),
            "payment_date": payment_date,
            "accrued_amount": gross,
            "pit_accrued": pit,
            "levy_accrued": levy,
            "paid_amount": gross,
            "pit_remitted": pit,
            "levy_remitted": levy,
            "payment_evidence_reference": f"TEST-PAYMENT-{index}",
            "tax_remittance_evidence_reference": f"TEST-TAX-{index}",
        })

    return report, readiness, evidence, {7: mapping}, source


async def real_postgresql_proof():
    raw = os.environ.get("DATABASE_URL")
    if not raw:
        raise AssertionError("Isolated PostgreSQL DATABASE_URL required")

    url = make_url(raw)

    if not url.drivername.startswith("postgresql"):
        raise AssertionError("PostgreSQL is required")

    # This test must never write to the source ERP database.
    if url.database in (None, "erp_db", "postgres"):
        raise AssertionError("Refusing non-isolated database")

    if not url.database.startswith("erp_r3_copy_"):
        raise AssertionError(
            "Expected regression-copy PostgreSQL database"
        )

    if url.drivername != "postgresql+asyncpg":
        url = url.set(drivername="postgresql+asyncpg")

    engine = create_async_engine(url, pool_pre_ping=True)

    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()

            try:
                database = await connection.scalar(
                    text("SELECT current_database()")
                )
                assert database == url.database

                report, readiness, evidence, approvals, source = scenario()

                rows = build_split_4df_draft_rows(
                    report=report,
                    readiness=readiness,
                    evidence=evidence,
                    approvals=approvals,
                )

                assert len(rows) == 2

                expected_source_record = {
                    "schema": "SOURCE_EVIDENCE_V1",
                    "company_id": report.company_id,
                    "payroll_period_id": report.payroll_period_id,
                    "report_kind": "4df",
                    "source": source,
                }

                expected_digest = hashlib.sha256(
                    canonical_json(
                        expected_source_record
                    ).encode("utf-8")
                ).hexdigest()

                await connection.execute(text("""
                    CREATE TEMPORARY TABLE
                        phase13_4df_split_acceptance (
                            row_no integer PRIMARY KEY,
                            company_id integer NOT NULL,
                            employee_id integer NOT NULL,
                            employment_contract_id integer NOT NULL,
                            payroll_calculation_id integer NOT NULL,
                            row_code text NOT NULL,
                            payload_json jsonb NOT NULL
                        ) ON COMMIT DROP
                """))

                for index, item in enumerate(rows, 1):
                    await connection.execute(
                        text("""
                            INSERT INTO
                                phase13_4df_split_acceptance (
                                    row_no,
                                    company_id,
                                    employee_id,
                                    employment_contract_id,
                                    payroll_calculation_id,
                                    row_code,
                                    payload_json
                                )
                            VALUES (
                                :row_no,
                                :company_id,
                                :employee_id,
                                :employment_contract_id,
                                :payroll_calculation_id,
                                :row_code,
                                CAST(:payload_json AS jsonb)
                            )
                        """),
                        {
                            "row_no": index,
                            **item,
                        },
                    )

                persisted = (
                    await connection.execute(text("""
                        SELECT
                            row_no,
                            company_id,
                            employee_id,
                            employment_contract_id,
                            payroll_calculation_id,
                            row_code,
                            payload_json::text
                        FROM phase13_4df_split_acceptance
                        ORDER BY row_no
                    """))
                ).all()

                assert len(persisted) == 2

                gross_total = 0
                pit_total = 0
                levy_total = 0
                paid_total = 0
                slice_numbers = set()
                replay_rows = []

                for stored in persisted:
                    (
                        row_no,
                        company_id,
                        employee_id,
                        contract_id,
                        calculation_id,
                        row_code,
                        payload_text,
                    ) = stored

                    payload = json.loads(payload_text)

                    assert row_no in (1, 2)
                    assert company_id == 11
                    assert employee_id == 44
                    assert contract_id == 55
                    assert calculation_id == 33
                    assert row_code == "4DF_SPLIT_REVIEWED_SOURCE_V1"

                    assert payload["source_sha256"] == expected_digest
                    assert payload["source_id"] == calculation_id
                    assert payload["slice_count"] == 2
                    assert payload["official_generation"] == "BLOCKED"
                    assert payload["currency_code"] == "UAH"

                    slice_numbers.add(payload["slice_number"])

                    # Integer cents avoid floating-point rounding.
                    def cents(value):
                        from decimal import Decimal
                        return int(Decimal(value) * 100)

                    gross_total += cents(payload["accrued_amount"])
                    pit_total += cents(payload["pit_accrued"])
                    levy_total += cents(payload["levy_accrued"])
                    paid_total += cents(payload["paid_amount"])

                    replay_rows.append(
                        SimpleNamespace(
                            company_id=company_id,
                            row_no=row_no,
                            row_code=row_code,
                            payload_json=payload_text,
                        )
                    )

                assert slice_numbers == {1, 2}
                assert gross_total == 150000
                assert pit_total == 27000
                assert levy_total == 7500
                assert paid_total == 150000

                print("REAL PG TWO-SLICE PERSISTENCE = PASS")
                print("REAL PG RE-READ FINANCIAL TOTALS = PASS")
                print("REAL PG SOURCE SHA256 BINDING = PASS")

                # Internal review rows must not be accepted as
                # finalized statutory declaration rows.
                with pytest.raises(
                    RegulatoryGeneratedIntegrityError
                ):
                    validate_mapped_report_rows(
                        report_kind="4df",
                        company_id=11,
                        period_id=22,
                        rows=replay_rows,
                        approved_mapping_ids=frozenset({7}),
                    )

                print("OFFICIAL ROW FINALIZATION BLOCKED = PASS")

                # Verify a broken split cannot reach persistence.
                broken = [
                    dict(entry) for entry in evidence
                ]
                broken[1]["pit_accrued"] = "89.99"

                with pytest.raises(FourDFSplitAllocationError):
                    build_split_4df_draft_rows(
                        report=report,
                        readiness=readiness,
                        evidence=broken,
                        approvals=approvals,
                    )

                print("BROKEN SPLIT RECONCILIATION REJECTED = PASS")
                print("OFFICIAL GENERATION = BLOCKED")

            finally:
                await transaction.rollback()

    finally:
        await engine.dispose()


def test_real_postgresql_split_financial_persistence():
    asyncio.run(real_postgresql_proof())
