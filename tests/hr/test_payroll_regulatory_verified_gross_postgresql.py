"""Positive and negative PostgreSQL source-bound gross E2E."""

import json
import os
import runpy

import pytest

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    create_async_engine,
    async_sessionmaker,
)

from app.core.config import settings

from app.services.payroll_regulatory_verified_gross_writer import (
    IndependentGrossSourceError,
    persist_gross_verified_internal_four_form_review,
)


@pytest.mark.asyncio
async def test_actual_calculation_gross_controls_internal_snapshot():
    assert os.environ.get("RUN_POSTGRES_E2E") == "1"

    engine = create_async_engine(settings.database_url)

    seed = runpy.run_path(
        "tests/hr/test_payroll_regulatory_reporting_postgresql.py"
    )["_seed_payroll"]

    helpers = runpy.run_path(
        "tests/hr/test_payroll_regulatory_internal_writer_e2e_postgresql.py"
    )

    create_report = helpers["create_report"]
    add_row = helpers["add_internal_row"]

    try:
        sessions = async_sessionmaker(
            engine, expire_on_commit=False
        )

        async with sessions() as db:
            database = (
                await db.execute(
                    text("SELECT current_database()")
                )
            ).scalar_one()

            assert database != "erp_db"

            (
                company,
                actor,
                employee,
                contract,
                period,
                payroll_input,
                calculation,
            ) = await seed(db)

            references = {}

            for form in ("D1", "4DF", "D5", "D6"):
                report_id = await create_report(
                    db,
                    company_id=company.id,
                    period_id=period.id,
                    form=form,
                    actor_id=actor.id,
                )

                references[form] = report_id

                await add_row(
                    db,
                    company_id=company.id,
                    report_id=report_id,
                    employee_id=employee.id,
                    form=form,
                    amount="10000.00",
                )

                if form in ("D1", "4DF"):
                    existing = (
                        await db.execute(
                            text("""
                                SELECT id, payload_json
                                FROM payroll_regulatory_report_rows
                                WHERE company_id = :cid
                                  AND payroll_regulatory_report_id = :rid
                            """),
                            {
                                "cid": company.id,
                                "rid": report_id,
                            },
                        )
                    ).mappings().one()

                    payload = json.loads(
                        existing["payload_json"]
                    )

                    payload["source"] = {
                        "type": "payroll_calculation",
                        "id": calculation.id,
                        "revision": 1,
                    }

                    payload["payroll_calculation_id"] = calculation.id

                    await db.execute(
                        text("""
                            UPDATE payroll_regulatory_report_rows
                            SET payroll_calculation_id = :calculation_id,
                                payload_json = :payload
                            WHERE company_id = :cid
                              AND id = :row_id
                        """),
                        {
                            "calculation_id": calculation.id,
                            "payload": json.dumps(payload),
                            "cid": company.id,
                            "row_id": existing["id"],
                        },
                    )

            args = {
                "company_id": company.id,
                "payroll_period_id": period.id,
                "payroll_period": "2026-10",
                "report_references": references,
            }

            first = (
                await persist_gross_verified_internal_four_form_review(
                    db,
                    **args,
                    request_key="gross-independent-e2e-v1",
                )
            )

            assert first["created"] is True
            assert first["independent_gross_verified"] is True
            assert first["tax_components_verified"] is False
            assert first["official_generation"] == "BLOCKED"

            replay = (
                await persist_gross_verified_internal_four_form_review(
                    db,
                    **args,
                    request_key="gross-independent-e2e-v1",
                )
            )

            assert replay["created"] is False
            assert replay["id"] == first["id"]

            # A substituted amount must not produce another
            # source-verified snapshot.
            row = (
                await db.execute(
                    text("""
                        SELECT id, payload_json
                        FROM payroll_regulatory_report_rows
                        WHERE company_id = :cid
                          AND payroll_regulatory_report_id = :rid
                    """),
                    {
                        "cid": company.id,
                        "rid": references["D1"],
                    },
                )
            ).mappings().one()

            altered = json.loads(row["payload_json"])
            altered["amounts"]["gross"] = "9999.00"

            await db.execute(
                text("""
                    UPDATE payroll_regulatory_report_rows
                    SET payload_json = :payload
                    WHERE company_id = :cid
                      AND id = :row_id
                """),
                {
                    "payload": json.dumps(altered),
                    "cid": company.id,
                    "row_id": row["id"],
                },
            )

            with pytest.raises(IndependentGrossSourceError):
                await persist_gross_verified_internal_four_form_review(
                    db,
                    **args,
                    request_key="gross-independent-e2e-v2",
                )

            # Roll back the entire synthetic test transaction.
            await db.rollback()

            print(
                "CURRENT PAYROLL GROSS = VERIFIED",
                "SOURCE-BOUND INSERT = PASS",
                "EXACT REPLAY = PASS",
                "TAMPERED AMOUNT = BLOCKED",
                "OFFICIAL GENERATION = BLOCKED",
                "TRANSACTION ROLLBACK = PASS",
                sep="\n",
                flush=True,
            )

    finally:
        await engine.dispose()
