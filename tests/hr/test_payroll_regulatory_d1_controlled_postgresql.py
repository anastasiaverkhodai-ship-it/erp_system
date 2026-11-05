"""Real PostgreSQL negative E2E for the D1 statutory gate.

The fixture contains a valid current payroll calculation and
matching internal D1/4DF gross rows, but no independently
verified statutory result components.

The stronger D1 gate must refuse to write a snapshot.
"""

import json
import os
import runpy

import pytest

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import (
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings

from app.services.payroll_regulatory_verified_gross_writer import (
    derive_independent_gross_controls,
)

from app.services.payroll_regulatory_d1_tax_controlled_writer import (
    persist_d1_tax_controlled_internal_review,
)


FORMS = ("D1", "4DF", "D5", "D6")


@pytest.mark.asyncio
async def test_d1_gate_rejects_missing_real_statutory_sources():
    assert os.environ.get("RUN_POSTGRES_E2E") == "1"

    # Use the already tested ERP PostgreSQL factories.
    seed = runpy.run_path(
        "tests/hr/"
        "test_payroll_regulatory_reporting_postgresql.py"
    )["_seed_payroll"]

    helpers = runpy.run_path(
        "tests/hr/"
        "test_payroll_regulatory_internal_writer_e2e_postgresql.py"
    )

    create_report = helpers["create_report"]
    add_internal_row = helpers["add_internal_row"]

    engine = create_async_engine(settings.database_url)

    try:
        sessions = async_sessionmaker(
            engine,
            expire_on_commit=False,
        )

        async with sessions() as db:
            name = (
                await db.execute(
                    text("SELECT current_database()")
                )
            ).scalar_one()

            assert name != "erp_db"

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

            for form in FORMS:
                report_id = await create_report(
                    db,
                    company_id=company.id,
                    period_id=period.id,
                    form=form,
                    actor_id=actor.id,
                )

                references[form] = report_id

                await add_internal_row(
                    db,
                    company_id=company.id,
                    report_id=report_id,
                    employee_id=employee.id,
                    form=form,
                    amount="10000.00",
                )

                if form not in ("D1", "4DF"):
                    continue

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
                            "rid": report_id,
                        },
                    )
                ).mappings().one()

                payload = json.loads(row["payload_json"])

                payload["source"] = {
                    "type": "payroll_calculation",
                    "id": calculation.id,
                    "revision": 1,
                }

                payload["payroll_calculation_id"] = (
                    calculation.id
                )

                await db.execute(
                    text("""
                        UPDATE payroll_regulatory_report_rows
                        SET payroll_calculation_id = :calc,
                            payload_json = :payload
                        WHERE company_id = :cid
                          AND id = :row_id
                    """),
                    {
                        "calc": calculation.id,
                        "payload": json.dumps(payload),
                        "cid": company.id,
                        "row_id": row["id"],
                    },
                )

            args = {
                "company_id": company.id,
                "payroll_period_id": period.id,
                "payroll_period": "2026-10",
                "report_references": references,
            }

            # Prove that gross matching is successful first.
            independent = (
                await derive_independent_gross_controls(
                    db,
                    **args,
                )
            )

            assert independent["D1"]["gross"] == "10000.00"
            assert independent["4DF"]["gross"] == "10000.00"

            print(
                "CURRENT PAYROLL GROSS = PASS",
                flush=True,
            )

            before = (
                await db.execute(
                    text("""
                        SELECT COUNT(*)
                        FROM payroll_regulatory_internal_snapshots
                        WHERE company_id = :cid
                          AND payroll_period_id = :pid
                    """),
                    {
                        "cid": company.id,
                        "pid": period.id,
                    },
                )
            ).scalar_one()

            assert before == 0

            # An incomplete statutory source must be rejected
            # even though gross is fully reconciled.
            with pytest.raises(Exception) as rejected:
                await persist_d1_tax_controlled_internal_review(
                    db,
                    **args,
                    request_key="d1-missing-statutory-e2e",
                )

            assert not isinstance(
                rejected.value,
                DBAPIError,
            ), (
                "Expected a source/domain rejection, "
                "not a PostgreSQL execution failure"
            )

            # A source rejection must leave the transaction
            # usable; no prior INSERT is allowed.
            after = (
                await db.execute(
                    text("""
                        SELECT COUNT(*)
                        FROM payroll_regulatory_internal_snapshots
                        WHERE company_id = :cid
                          AND payroll_period_id = :pid
                    """),
                    {
                        "cid": company.id,
                        "pid": period.id,
                    },
                )
            ).scalar_one()

            assert after == 0

            await db.rollback()

            print(
                "INCOMPLETE STATUTORY SOURCES = BLOCKED",
                "D1 WRITER SNAPSHOT COUNT = 0",
                "REAL POSTGRESQL TRANSACTION = PASS",
                "OFFICIAL GENERATION = BLOCKED",
                sep="\n",
                flush=True,
            )

    finally:
        await engine.dispose()
