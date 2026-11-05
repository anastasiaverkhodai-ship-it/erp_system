"""Real PostgreSQL HR inventory -> D5/D6 immutable snapshot E2E."""

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

from app.services.payroll_regulatory_d5_d6_evidence_bound_writer import (
    persist_d5_d6_evidence_bound_review,
    verify_anchor,
)

from app.services.payroll_regulatory_internal_reconciliation import (
    InternalSnapshot,
    verify_internal_snapshot,
)


@pytest.mark.asyncio
async def test_real_d5_d6_hr_inventory_bound_into_snapshot():
    assert os.environ.get("RUN_POSTGRES_E2E") == "1"

    seed = runpy.run_path(
        "tests/hr/test_payroll_regulatory_reporting_postgresql.py"
    )["_seed_payroll"]

    helpers = runpy.run_path(
        "tests/hr/test_payroll_regulatory_internal_writer_e2e_postgresql.py"
    )

    create_report = helpers["create_report"]
    add_row = helpers["add_internal_row"]

    engine = create_async_engine(settings.database_url)

    try:
        sessions = async_sessionmaker(
            engine,
            expire_on_commit=False,
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

                if form in ("D1", "4DF"):
                    payload["source"] = {
                        "type": "payroll_calculation",
                        "id": calculation.id,
                        "revision": 1,
                    }

                    payload["payroll_calculation_id"] = calculation.id

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

            first = await persist_d5_d6_evidence_bound_review(
                db,
                **args,
                request_key="hr-evidence-e2e-v1",
            )

            assert first["created"] is True
            assert first["d5_d6_hr_inventory_bound"] is True
            assert first["d5_statutory_mapping_verified"] is False
            assert first["d6_applicability_verified"] is False
            assert first["official_generation"] == "BLOCKED"

            stored = (
                await db.execute(
                    text("""
                        SELECT version, payload_json, snapshot_sha256
                        FROM payroll_regulatory_internal_snapshots
                        WHERE company_id = :cid
                          AND id = :sid
                    """),
                    {
                        "cid": company.id,
                        "sid": first["id"],
                    },
                )
            ).mappings().one()

            content = verify_internal_snapshot(
                InternalSnapshot(
                    company_id=company.id,
                    payroll_period="2026-10",
                    version=stored["version"],
                    canonical_json=stored["payload_json"],
                    sha256=stored["snapshot_sha256"].strip(),
                )
            )

            hr_anchors = []

            for form in ("D5", "D6"):
                rows = [
                    r for r in content["rows"]
                    if r["form"] == form
                ]

                assert rows

                for row in rows:
                    anchor = row["attributes"][
                        "hr_inventory_evidence"
                    ]

                    checked = verify_anchor(anchor)
                    assert checked["company"] == company.id
                    hr_anchors.append(anchor)

            assert len(set(hr_anchors)) == 1

            print(
                "REAL HR INVENTORY = VERIFIED",
                "IMMUTABLE D5/D6 EVIDENCE = PASS",
                "HR SOURCE SHA256 = PASS",
                sep="\n",
                flush=True,
            )

            replay = await persist_d5_d6_evidence_bound_review(
                db,
                **args,
                request_key="hr-evidence-e2e-v1",
            )

            assert replay["created"] is False
            assert replay["id"] == first["id"]

            second = await persist_d5_d6_evidence_bound_review(
                db,
                **args,
                request_key="hr-evidence-e2e-v2",
            )

            assert second["created"] is True
            assert second["version"] == 2

            previous = (
                await db.execute(
                    text("""
                        SELECT previous_snapshot_id
                        FROM payroll_regulatory_internal_snapshots
                        WHERE company_id = :cid
                          AND id = :sid
                    """),
                    {
                        "cid": company.id,
                        "sid": second["id"],
                    },
                )
            ).scalar_one()

            assert previous == first["id"]

            await db.rollback()

            count = (
                await db.execute(
                    text("""
                        SELECT COUNT(*)
                        FROM payroll_regulatory_internal_snapshots
                        WHERE id IN (:first_id, :second_id)
                    """),
                    {
                        "first_id": first["id"],
                        "second_id": second["id"],
                    },
                )
            ).scalar_one()

            assert count == 0

            print(
                "HR EVIDENCE REPLAY = PASS",
                "D5/D6 VERSION CHAIN = PASS",
                "HR TRANSACTION ROLLBACK = PASS",
                "OFFICIAL GENERATION = BLOCKED",
                sep="\n",
                flush=True,
            )
    finally:
        await engine.dispose()
