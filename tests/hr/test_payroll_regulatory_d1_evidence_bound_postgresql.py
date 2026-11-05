"""Positive real-PostgreSQL D1 source-gated internal snapshots.

Uses the ERP statutory payroll factory, not a mocked D1 review.
The existing D1 service verifies persisted calculation sources
and statutory component lines before the internal writer runs.

This E2E proves:
- actual current statutory payroll source acceptance
- independent gross reconciliation
- D1-controlled internal snapshot INSERT
- exact idempotent replay
- append-only version chain
- existing snapshot digest verification
- tenant-scoped persistence
- transaction rollback

It does not prove separate PIT/military components,
4DF posted-payment coverage, D5/D6 official applicability,
or a persisted immutable copy of the D1 tax evidence.
"""

import json
import os
import runpy
from decimal import Decimal

import pytest

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings

from app.services.payroll_regulatory_d1_evidence_bound_writer import (
    persist_d1_evidence_bound_review,
)

from app.services.payroll_regulatory_d1_review_projection import (
    build_verified_d1_review,
)

from app.services.payroll_regulatory_internal_reconciliation import (
    InternalSnapshot,
    verify_internal_snapshot,
)


@pytest.mark.asyncio
async def test_positive_source_bound_immutable_d1_evidence():
    assert os.environ.get("RUN_POSTGRES_E2E") == "1"

    # Use the complete statutory payroll factory. Its deduction
    # authorization must follow the current pending -> approved
    # write services, never a direct approved INSERT.
    from unittest.mock import patch

    deduction_helpers = runpy.run_path(
        "tests/hr/"
        "test_payroll_deduction_execution_postgresql.py"
    )

    original_authorize = deduction_helpers["authorize"]
    complete_seed = deduction_helpers["seed"]

    async def authorize_with_workflow(
        db, company, user, contract, claim_amount=None,
    ):
        if claim_amount is None:
            return await original_authorize(
                db, company, user, contract, migrated=True,
            )
        return await original_authorize(
            db, company, user, contract,
            claim_amount=claim_amount,
            migrated=True,
        )

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
            current_database = (
                await db.execute(
                    text("SELECT current_database()")
                )
            ).scalar_one()

            assert current_database != "erp_db"

            # Scoped to this single seed call. The original
            # module global is automatically restored afterward.
            with patch.dict(
                complete_seed.__globals__,
                {"authorize": authorize_with_workflow},
            ):
                (
                    company,
                    actor,
                    contract,
                    calculation,
                    bank,
                    beneficiary,
                    claim,
                ) = await complete_seed(db)

            assert company.id > 0
            assert actor.id > 0
            assert contract.company_id == company.id
            assert calculation.company_id == company.id
            assert calculation.payroll_period_id > 0

            print(
                "PENDING-TO-APPROVED RULE WORKFLOW = PASS",
                flush=True,
            )

            period = (
                await db.execute(
                    text("""
                        SELECT
                            id,
                            company_id,
                            year,
                            month
                        FROM payroll_periods
                        WHERE id = :pid
                          AND company_id = :cid
                    """),
                    {
                        "pid": calculation.payroll_period_id,
                        "cid": company.id,
                    },
                )
            ).mappings().one()

            payroll_period = (
                f"{period['year']:04d}-"
                f"{period['month']:02d}"
            )

            # Required by the real regulatory readiness service.
            # This is the same legitimate fixture preparation used
            # by test_payroll_regulatory_d1_dated_sources_postgresql.
            from app.models.employee import Employee

            employee = await db.get(Employee, contract.employee_id)
            assert employee is not None
            assert employee.company_id == company.id

            if not employee.tax_number:
                employee.tax_number = "1234567890"
                await db.flush()

            assert employee.tax_number
            print(
                "EMPLOYEE TAX ID SOURCE = PASS",
                flush=True,
            )

            employee_id = contract.employee_id
            gross = Decimal(
                str(calculation.gross_amount)
            ).quantize(Decimal("0.01"))

            assert gross > 0

            # The actual D1 source path queries persisted
            # payroll statutory results and component lines.
            verified_review = await build_verified_d1_review(
                db,
                company_id=company.id,
                payroll_period_id=period["id"],
            )

            assert verified_review["report_kind"] == "d1"
            assert verified_review["review_only"] is True
            assert (
                verified_review["official_statutory_generation"]
                == "BLOCKED"
            )
            assert verified_review["source_count"] > 0
            assert verified_review["rows"]

            references = {}

            for form in ("D1", "4DF", "D5", "D6"):
                report_id = await create_report(
                    db,
                    company_id=company.id,
                    period_id=period["id"],
                    form=form,
                    actor_id=actor.id,
                )

                references[form] = report_id

                await add_internal_row(
                    db,
                    company_id=company.id,
                    report_id=report_id,
                    employee_id=employee_id,
                    form=form,
                    amount=str(gross),
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
                payload["payroll_period"] = payroll_period

                if form in ("D1", "4DF"):
                    payload["source"] = {
                        "type": "payroll_calculation",
                        "id": calculation.id,
                        "revision": 1,
                    }

                    payload["payroll_calculation_id"] = (
                        calculation.id
                    )
                    payload["amounts"] = {
                        "gross": str(gross)
                    }

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
                            "payload": json.dumps(
                                payload,
                                sort_keys=True,
                            ),
                            "cid": company.id,
                            "row_id": row["id"],
                        },
                    )

                else:
                    await db.execute(
                        text("""
                            UPDATE payroll_regulatory_report_rows
                            SET payload_json = :payload
                            WHERE company_id = :cid
                              AND id = :row_id
                        """),
                        {
                            "payload": json.dumps(
                                payload,
                                sort_keys=True,
                            ),
                            "cid": company.id,
                            "row_id": row["id"],
                        },
                    )

            args = {
                "company_id": company.id,
                "payroll_period_id": period["id"],
                "payroll_period": payroll_period,
                "report_references": references,
            }

            # POSITIVE real PostgreSQL entrypoint.
            first = (
                await persist_d1_evidence_bound_review(
                    db,
                    **args,
                    request_key="d1-real-full-sources-v1",
                )
            )

            assert first["created"] is True
            assert first["version"] == 1
            assert first["d1_statutory_review_verified"] is True
            assert first["independent_gross_verified"] is True
            assert first["official_generation"] == "BLOCKED"

            control = first["d1_source_control"]

            assert control["d1_source_review_checked"] is True
            assert control["gross_independently_reconciled"] is True
            assert control["source_count"] > 0
            assert Decimal(control["gross_total"]) == gross
            assert control["separate_pit_verified"] is False
            assert control["separate_military_levy_verified"] is False

            # Verify immutable payload is stored in PostgreSQL,
            # not only returned from an in-memory review.
            stored = (
                await db.execute(
                    text("""
                        SELECT
                            id,
                            company_id,
                            payroll_period_id,
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
                        "cid": company.id,
                        "sid": first["id"],
                    },
                )
            ).mappings().one()

            assert stored["status"] == "internal_review_only"
            assert stored["company_id"] == company.id
            assert stored["payroll_period_id"] == period["id"]
            assert stored["version"] == 1
            assert stored["previous_snapshot_id"] is None

            persisted_snapshot = InternalSnapshot(
                company_id=company.id,
                payroll_period=payroll_period,
                version=stored["version"],
                canonical_json=stored["payload_json"],
                sha256=stored["snapshot_sha256"].strip(),
            )

            contents = verify_internal_snapshot(
                persisted_snapshot
            )

            assert len(contents["rows"]) == 4

            from app.services.payroll_regulatory_d1_evidence_bound_writer import (
                verify_anchor,
            )

            anchored_d1 = [
                row for row in contents["rows"]
                if row["form"] == "D1"
            ]

            assert anchored_d1

            for row in anchored_d1:
                serialized = row["attributes"][
                    "d1_aggregate_evidence"
                ]

                assert isinstance(serialized, str)

                from app.services.payroll_regulatory_d1_evidence_bound_writer import (
                    deserialize_anchor,
                )

                anchor = deserialize_anchor(serialized)

                assert verify_anchor(anchor) is True
                assert (
                    anchor["sha256"]
                    == first["d1_aggregate_evidence_sha256"]
                )

            assert first["d1_aggregate_evidence_in_snapshot"] is True

            print(
                "IMMUTABLE D1 AGGREGATE EVIDENCE = PASS",
                flush=True,
            )
            assert (
                contents["official_generation_authorized"]
                is False
            )

            print(
                "REAL D1 STATUTORY COMPONENT SOURCES = PASS",
                "POSITIVE D1 CONTROLLED WRITER = PASS",
                "PERSISTED SNAPSHOT DIGEST = PASS",
                sep="\n",
                flush=True,
            )

            # Reusing the key cannot silently add a new version.
            replay = (
                await persist_d1_evidence_bound_review(
                    db,
                    **args,
                    request_key="d1-real-full-sources-v1",
                )
            )

            assert replay["created"] is False
            assert replay["id"] == first["id"]

            # New request produces linked immutable version.
            second = (
                await persist_d1_evidence_bound_review(
                    db,
                    **args,
                    request_key="d1-real-full-sources-v2",
                )
            )

            assert second["created"] is True
            assert second["version"] == 2

            previous_id = (
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

            assert previous_id == first["id"]

            cross_tenant_count = (
                await db.execute(
                    text("""
                        SELECT COUNT(*)
                        FROM payroll_regulatory_internal_snapshots
                        WHERE company_id = :other
                          AND id IN (:first, :second)
                    """),
                    {
                        "other": company.id + 100000000,
                        "first": first["id"],
                        "second": second["id"],
                    },
                )
            ).scalar_one()

            assert cross_tenant_count == 0

            # All changes are transactional and test-only.
            await db.rollback()

            retained = (
                await db.execute(
                    text("""
                        SELECT COUNT(*)
                        FROM payroll_regulatory_internal_snapshots
                        WHERE id IN (:first, :second)
                    """),
                    {
                        "first": first["id"],
                        "second": second["id"],
                    },
                )
            ).scalar_one()

            assert retained == 0

            print(
                "IDEMPOTENT D1 REPLAY = PASS",
                "IMMUTABLE VERSION CHAIN = PASS",
                "TENANT SCOPING = PASS",
                "TRANSACTION ROLLBACK = PASS",
                "OFFICIAL STATUTORY GENERATION = BLOCKED",
                sep="\n",
                flush=True,
            )

    finally:
        await engine.dispose()
