"""Real isolated PostgreSQL acceptance for the internal snapshot writer.

The monetary fixture below is synthetic. It proves the database
workflow, not independent statutory/payroll financial approval.

All transactions are rolled back. No production DB is accessed.
"""

import json
import os
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

import pytest

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import (
    create_async_engine,
    async_sessionmaker,
)

from app.core.config import settings

from app.services.payroll_regulatory_internal_snapshot_writer import (
    persist_internal_four_form_review,
)

from app.services.payroll_regulatory_internal_reconciliation import (
    InternalSnapshot,
    verify_internal_snapshot,
)


FORMS = ("D1", "4DF", "D5", "D6")
KINDS = {
    "D1": "d1",
    "4DF": "4df",
    "D5": "d5",
    "D6": "d6",
}

ROW_CODE = "ERP_INTERNAL_RECONCILIATION_V1"
SCHEMA = "erp.internal-regulatory-row.v1"


def expected_totals(gross="100.00"):
    return {
        "D1": {"gross": gross},
        "4DF": {"gross": gross},
        "D5": {},
        "D6": {},
    }


async def create_report(db, *, company_id, period_id, form, actor_id):
    current = (
        await db.execute(
            text("""
                SELECT COALESCE(MAX(revision), 0)
                FROM payroll_regulatory_reports
                WHERE company_id = :cid
                  AND payroll_period_id = :pid
                  AND report_kind = :kind
            """),
            {
                "cid": company_id,
                "pid": period_id,
                "kind": KINDS[form],
            },
        )
    ).scalar_one()

    result = await db.execute(
        text("""
            INSERT INTO payroll_regulatory_reports (
                company_id,
                payroll_period_id,
                report_kind,
                revision,
                status,
                created_by,
                created_at
            )
            VALUES (
                :cid, :pid, :kind, :revision,
                'draft', :created_by, CURRENT_TIMESTAMP
            )
            RETURNING id
        """),
        {
            "cid": company_id,
            "pid": period_id,
            "kind": KINDS[form],
            "revision": current + 1,
            "created_by": actor_id,
        },
    )

    return result.scalar_one()


async def add_internal_row(
    db,
    *,
    company_id,
    report_id,
    employee_id,
    form,
    amount="100.00",
):
    payload = {
        "schema": SCHEMA,
        "form": form,
        "company_id": company_id,
        "payroll_period": "2026-10",
        "row_key": f"{form}-internal-e2e",
        "employee_id": employee_id,
        "employment_contract_id": None,
        "payroll_calculation_id": None,
        "source": {
            "type": "employee_test_evidence",
            "id": employee_id,
            "revision": 1,
        },
        "document_reference": "ERP-ISOLATED-TEST",
        "document_date": "2026-10-31",
        "amounts": (
            {"gross": amount}
            if form in ("D1", "4DF")
            else {}
        ),
        "attributes": {
            "fixture": "synthetic_internal_only",
        },
        "correction_of": None,
    }

    await db.execute(
        text("""
            INSERT INTO payroll_regulatory_report_rows (
                company_id,
                payroll_regulatory_report_id,
                row_no,
                employee_id,
                employment_contract_id,
                payroll_calculation_id,
                row_code,
                payload_json,
                created_at
            )
            VALUES (
                :cid, :rid, 1, :eid,
                NULL, NULL, :code, :payload,
                CURRENT_TIMESTAMP
            )
        """),
        {
            "cid": company_id,
            "rid": report_id,
            "eid": employee_id,
            "code": ROW_CODE,
            "payload": json.dumps(
                payload,
                sort_keys=True,
            ),
        },
    )


@pytest.mark.asyncio
async def test_real_postgresql_writer_full_lifecycle():
    assert os.environ.get("RUN_POSTGRES_E2E") == "1"

    engine = create_async_engine(settings.database_url)

    try:
        sessions = async_sessionmaker(
            engine,
            expire_on_commit=False,
        )

        async with sessions() as db:
            identity = (
                await db.execute(
                    text("""
                        SELECT
                            current_database(),
                            current_user
                    """)
                )
            ).one()

            assert identity[0] != "erp_db"

            print(
                "ISOLATED DATABASE =",
                identity[0],
                flush=True,
            )

            revision = (
                await db.execute(
                    text("""
                        SELECT version_num
                        FROM public.alembic_version
                    """)
                )
            ).scalar_one()

            config = Config()
            config.set_main_option(
                "script_location", str(Path(__file__).resolve().parents[2] / "alembic")
            )
            assert revision == ScriptDirectory.from_config(config).get_current_head()

            # Reuse actual tenant/period/employee identities
            # from the disposable copy. No sample payroll amounts
            # are treated as externally verified statutory totals.
            import runpy

            seed_module = runpy.run_path(
                "tests/hr/test_payroll_regulatory_reporting_postgresql.py"
            )
            seed = seed_module["_seed_payroll"]

            (
                company,
                actor,
                employee,
                contract,
                period,
                payroll_input,
                calculation,
            ) = await seed(db)

            company_id = company.id
            period_id = period.id
            employee_id = employee.id

            assert contract.company_id == company_id
            assert contract.employee_id == employee_id
            assert period.company_id == company_id
            assert calculation.company_id == company_id
            assert calculation.payroll_period_id == period_id

            print(
                "REAL ERP SEED = PASS",
                flush=True,
            )

            references = {}

            for form in FORMS:
                report_id = await create_report(
                    db,
                    company_id=company_id,
                    period_id=period_id,
                    form=form,
                    actor_id=actor.id,
                )

                references[form] = report_id

                await add_internal_row(
                    db,
                    company_id=company_id,
                    report_id=report_id,
                    employee_id=employee_id,
                    form=form,
                )

            args = {
                "company_id": company_id,
                "payroll_period_id": period_id,
                "payroll_period": "2026-10",
                "report_references": references,
                "expected_source_totals": expected_totals(),
            }

            # 1. Positive real INSERT.
            first = await persist_internal_four_form_review(
                db,
                **args,
                request_key="regulatory-e2e-first-v1",
            )

            assert first["created"] is True
            assert first["version"] == 1
            assert first["official_generation"] == "BLOCKED"

            stored = (
                await db.execute(
                    text("""
                        SELECT
                            id,
                            version,
                            payload_json,
                            snapshot_sha256,
                            previous_snapshot_id,
                            status
                        FROM payroll_regulatory_internal_snapshots
                        WHERE company_id = :cid
                          AND id = :sid
                    """),
                    {
                        "cid": company_id,
                        "sid": first["id"],
                    },
                )
            ).mappings().one()

            assert stored["status"] == "internal_review_only"
            assert stored["previous_snapshot_id"] is None

            snapshot = InternalSnapshot(
                company_id=company_id,
                payroll_period="2026-10",
                version=stored["version"],
                canonical_json=stored["payload_json"],
                sha256=stored["snapshot_sha256"].strip(),
            )

            payload = verify_internal_snapshot(snapshot)
            assert len(payload["rows"]) == 4

            # 2. Exact replay cannot create a duplicate.
            replay = await persist_internal_four_form_review(
                db,
                **args,
                request_key="regulatory-e2e-first-v1",
            )

            assert replay["id"] == first["id"]
            assert replay["created"] is False

            # 3. Reusing same key with changed control totals fails.
            with pytest.raises(Exception) as changed:
                await persist_internal_four_form_review(
                    db,
                    **{
                        **args,
                        "expected_source_totals": expected_totals(
                            "101.00"
                        ),
                    },
                    request_key="regulatory-e2e-first-v1",
                )

            assert not isinstance(
                changed.value,
                DBAPIError,
            ), "Domain conflict should fail before SQL integrity error"

            # 4. Next immutable version and predecessor.
            second = await persist_internal_four_form_review(
                db,
                **args,
                request_key="regulatory-e2e-second-v2",
            )

            assert second["created"] is True
            assert second["version"] == 2
            assert second["id"] != first["id"]

            parent = (
                await db.execute(
                    text("""
                        SELECT previous_snapshot_id
                        FROM payroll_regulatory_internal_snapshots
                        WHERE company_id = :cid
                          AND id = :sid
                    """),
                    {
                        "cid": company_id,
                        "sid": second["id"],
                    },
                )
            ).scalar_one()

            assert parent == first["id"]

            replay_second = (
                await persist_internal_four_form_review(
                    db,
                    **args,
                    request_key="regulatory-e2e-second-v2",
                )
            )

            assert replay_second["id"] == second["id"]
            assert replay_second["created"] is False

            # 5. Direct SQL cannot rewrite immutable history.
            # Savepoints isolate intentionally rejected statements.
            for sql in (
                """
                UPDATE payroll_regulatory_internal_snapshots
                SET status = 'internal_review_only'
                WHERE id = :sid
                """,
                """
                DELETE FROM payroll_regulatory_internal_snapshots
                WHERE id = :sid
                """,
            ):
                nested = await db.begin_nested()

                try:
                    with pytest.raises(DBAPIError):
                        await db.execute(
                            text(sql),
                            {"sid": first["id"]},
                        )
                finally:
                    await nested.rollback()

            nested = await db.begin_nested()

            try:
                with pytest.raises(DBAPIError):
                    await db.execute(
                        text("""
                            TRUNCATE TABLE
                            payroll_regulatory_internal_snapshots
                        """)
                    )
            finally:
                await nested.rollback()

            # 6. Cross-tenant read is empty.
            cross = (
                await db.execute(
                    text("""
                        SELECT COUNT(*)
                        FROM payroll_regulatory_internal_snapshots
                        WHERE company_id = :other_company
                          AND id = :sid
                    """),
                    {
                        "other_company": company_id + 100000000,
                        "sid": first["id"],
                    },
                )
            ).scalar_one()

            assert cross == 0

            # 7. Draft reports remain draft.
            statuses = (
                await db.execute(
                    text("""
                        SELECT status
                        FROM payroll_regulatory_reports
                        WHERE company_id = :cid
                          AND id IN (
                              :d1, :fourdf, :d5, :d6
                          )
                    """),
                    {
                        "cid": company_id,
                        "d1": references["D1"],
                        "fourdf": references["4DF"],
                        "d5": references["D5"],
                        "d6": references["D6"],
                    },
                )
            ).scalars().all()

            assert statuses == ["draft"] * 4

            # 8. Caller owns transaction: ROLLBACK removes
            # all newly created test records.
            await db.rollback()

            after_rollback = (
                await db.execute(
                    text("""
                        SELECT COUNT(*)
                        FROM payroll_regulatory_internal_snapshots
                        WHERE company_id = :cid
                          AND id IN (:first_id, :second_id)
                    """),
                    {
                        "cid": company_id,
                        "first_id": first["id"],
                        "second_id": second["id"],
                    },
                )
            ).scalar_one()

            assert after_rollback == 0

            print(
                "POSITIVE INSERT = PASS",
                "EXACT REPLAY = PASS",
                "CONFLICT = PASS",
                "SECOND VERSION = PASS",
                "IMMUTABILITY = PASS",
                "DRAFT GUARD = PASS",
                "ROLLBACK = PASS",
                sep="\n",
                flush=True,
            )

    finally:
        await engine.dispose()
