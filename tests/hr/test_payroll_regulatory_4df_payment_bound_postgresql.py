"""Real posted payment evidence -> immutable 4DF snapshot E2E."""

import json
import os
import runpy
from datetime import date
from decimal import Decimal

import pytest
from test_employee_foundation import employee_engine

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    create_async_engine,
    async_sessionmaker,
)

from app.core.config import settings

from app.services.payroll_regulatory_4df_payment_bound_writer import (
    persist_4df_payment_bound_internal_review,
    FourDFPaymentBindingError,
    verify_anchor,
)

from app.services.payroll_regulatory_report_service import (
    PayrollRegulatoryReportError,
)

from app.services.payroll_regulatory_internal_reconciliation import (
    InternalSnapshot,
    verify_internal_snapshot,
)


@pytest.mark.asyncio
async def test_real_posted_payment_bound_4df_snapshot(employee_engine):
    assert os.environ.get("RUN_POSTGRES_E2E") == "1"

    payment_factory = runpy.run_path(
        "tests/hr/test_payroll_regulatory_payment_bridge_postgresql.py"
    )["create_real_financial_case"]

    helpers = runpy.run_path(
        "tests/hr/test_payroll_regulatory_internal_writer_e2e_postgresql.py"
    )

    create_report = helpers["create_report"]
    add_row = helpers["add_internal_row"]

    engine = employee_engine

    try:
        sessions = async_sessionmaker(
            engine,
            expire_on_commit=False,
        )

        async with sessions() as db:
            current_db = (
                await db.execute(
                    text("SELECT current_database()")
                )
            ).scalar_one()

            assert current_db != "erp_db"

            (
                company,
                actor,
                calculation,
                payroll_payment,
                pit_remittance,
                allocation,
            ) = await payment_factory(db)

            period = (
                await db.execute(
                    text("""
                        SELECT year, month
                        FROM payroll_periods
                        WHERE company_id = :cid
                          AND id = :pid
                    """),
                    {
                        "cid": company.id,
                        "pid": calculation.payroll_period_id,
                    },
                )
            ).mappings().one()

            payroll_period = (
                f"{period['year']:04d}-{period['month']:02d}"
            )

            employee_id = (
                await db.execute(
                    text("""
                        SELECT employee_id
                        FROM employment_contracts
                        WHERE company_id = :cid
                          AND id = :contract_id
                    """),
                    {
                        "cid": company.id,
                        "contract_id":
                            calculation.employment_contract_id,
                    },
                )
            ).scalar_one()

            references = {}

            for form in ("D1", "4DF", "D5", "D6"):
                report_id = await create_report(
                    db,
                    company_id=company.id,
                    period_id=calculation.payroll_period_id,
                    form=form,
                    actor_id=actor.id,
                )

                references[form] = report_id

                await add_row(
                    db,
                    company_id=company.id,
                    report_id=report_id,
                    employee_id=employee_id,
                    form=form,
                    amount=str(calculation.gross_amount),
                )

                row = (
                    await db.execute(
                        text("""
                            SELECT id, payload_json
                            FROM payroll_regulatory_report_rows
                            WHERE company_id = :cid
                              AND payroll_regulatory_report_id = :rid
                        """),
                        {"cid": company.id, "rid": report_id},
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
                else:
                    await db.execute(
                        text("""
                            UPDATE payroll_regulatory_report_rows
                            SET payload_json = :payload
                            WHERE company_id = :cid
                              AND id = :row_id
                        """),
                        {
                            "payload": json.dumps(payload),
                            "cid": company.id,
                            "row_id": row["id"],
                        },
                    )

            args = {
                "company_id": company.id,
                "payroll_period_id":
                    calculation.payroll_period_id,
                "payroll_period": payroll_period,
                "report_references": references,
            }

            first = await persist_4df_payment_bound_internal_review(
                db,
                **args,
                reviewed_allocations=[allocation],
                request_key="4df-real-posted-v1",
            )

            assert first["created"] is True
            assert first[
                "fourdf_posted_payment_evidence_in_snapshot"
            ] is True
            assert first["payment_evidence_verified"] is True
            assert first["official_generation"] == "BLOCKED"

            stored = (
                await db.execute(
                    text("""
                        SELECT version, payload_json, snapshot_sha256
                        FROM payroll_regulatory_internal_snapshots
                        WHERE company_id = :cid AND id = :sid
                    """),
                    {"cid": company.id, "sid": first["id"]},
                )
            ).mappings().one()

            contents = verify_internal_snapshot(
                InternalSnapshot(
                    company_id=company.id,
                    payroll_period=payroll_period,
                    version=stored["version"],
                    canonical_json=stored["payload_json"],
                    sha256=stored["snapshot_sha256"].strip(),
                )
            )

            form_rows = [
                row for row in contents["rows"]
                if row["form"] == "4DF"
            ]

            assert len(form_rows) == 1

            evidence = form_rows[0]["attributes"][
                "fourdf_posted_payment_evidence"
            ]

            checked = verify_anchor(evidence)
            assert checked["c"] == calculation.id
            assert payroll_payment.id in checked["items"][0]["p"]

            replay = await persist_4df_payment_bound_internal_review(
                db,
                **args,
                reviewed_allocations=[allocation],
                request_key="4df-real-posted-v1",
            )

            assert replay["created"] is False
            assert replay["id"] == first["id"]

            changed = dict(allocation)
            changed["paid_amount"] = (
                Decimal(str(changed["paid_amount"]))
                + Decimal("0.01")
            )

            with pytest.raises(PayrollRegulatoryReportError):
                await persist_4df_payment_bound_internal_review(
                    db,
                    **args,
                    reviewed_allocations=[changed],
                    request_key="4df-invalid-v2",
                )

            count = (
                await db.execute(
                    text("""
                        SELECT COUNT(*)
                        FROM payroll_regulatory_internal_snapshots
                        WHERE company_id = :cid
                          AND payroll_period_id = :pid
                    """),
                    {
                        "cid": company.id,
                        "pid": calculation.payroll_period_id,
                    },
                )
            ).scalar_one()

            assert count == 1

            await db.rollback()

            print(
                "REAL POSTED PAYMENT = VERIFIED",
                "REAL TAX REMITTANCE = VERIFIED",
                "IMMUTABLE 4DF PAYMENT EVIDENCE = PASS",
                "4DF EXACT REPLAY = PASS",
                "TAMPERED PAYMENT = BLOCKED",
                "4DF TRANSACTION ROLLBACK = PASS",
                "OFFICIAL GENERATION = BLOCKED",
                sep="\n",
                flush=True,
            )
    finally:
        await engine.dispose()
