"""Combined four-form immutable snapshot on real PostgreSQL."""

import json
import os
import runpy

from unittest.mock import patch

import pytest

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    create_async_engine,
    async_sessionmaker,
)

from app.core.config import settings
from app.models.employee import Employee

from app.services.payroll_regulatory_combined_evidence_writer import (
    persist_combined_four_form_review,
    verify_combined_persisted_snapshot,
)

from app.services.payroll_regulatory_d1_evidence_bound_writer import (
    deserialize_anchor,
)
from app.services.payroll_regulatory_pit_levy_evidence_writer import (
    verify_anchor as verify_tax_anchor,
)

from app.services.payroll_regulatory_4df_payment_bound_writer import (
    verify_anchor as verify_payment_anchor,
)

from app.services.payroll_regulatory_d5_d6_evidence_bound_writer import (
    verify_anchor as verify_hr_anchor,
)


@pytest.mark.asyncio
async def test_real_combined_four_form_immutable_evidence():
    assert os.environ.get("RUN_POSTGRES_E2E") == "1"

    deduction_helpers = runpy.run_path(
        "tests/hr/test_payroll_deduction_execution_postgresql.py"
    )

    original_payroll_setup = deduction_helpers["payroll_setup"]

    financial_helpers = runpy.run_path(
        "tests/hr/test_payroll_regulatory_payment_bridge_postgresql.py"
    )

    payment_factory = financial_helpers[
        "create_real_financial_case"
    ]

    row_helpers = runpy.run_path(
        "tests/hr/test_payroll_regulatory_internal_writer_e2e_postgresql.py"
    )

    create_report = row_helpers["create_report"]
    add_row = row_helpers["add_internal_row"]

    async def verified_statutory_setup(db):
        (
            company,
            actor,
            contract,
            calculation,
            accrual,
            accounts,
            bank,
        ) = await original_payroll_setup(db)

        employee = await db.get(
            Employee,
            contract.employee_id,
        )

        assert employee is not None
        assert employee.company_id == company.id

        if not employee.tax_number:
            employee.tax_number = "1234567890"
            await db.flush()

        # This combined report fixture does not exercise
        # enforcement deductions. Creating an enforcement
        # instruction here would cause payroll disbursement
        # to invoke a separate, legally gated deduction path.
        # Keep the real statutory payroll calculation,
        # employee identity and payment journals instead.
        print(
            "UNRELATED ENFORCEMENT DEDUCTION = EXCLUDED",
            flush=True,
        )

        return (
            company,
            actor,
            contract,
            calculation,
            accrual,
            accounts,
            bank,
        )

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

            # Same payroll calculation produces BOTH:
            # 1. verified statutory payroll sources
            # 2. actual posted payment and remittance journals.
            with patch.dict(
                payment_factory.__globals__,
                {"setup": verified_statutory_setup},
            ):
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
                        SELECT id, year, month
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

            label = (
                f"{period['year']:04d}-"
                f"{period['month']:02d}"
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

            gross = str(calculation.gross_amount)

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

                await add_row(
                    db,
                    company_id=company.id,
                    report_id=report_id,
                    employee_id=employee_id,
                    form=form,
                    amount=gross,
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
                payload["payroll_period"] = label

                if form in ("D1", "4DF"):
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
                "payroll_period_id": period["id"],
                "payroll_period": label,
                "report_references": references,
                "reviewed_allocations": [allocation],
            }

            first = await persist_combined_four_form_review(
                db,
                **args,
                request_key="combined-four-form-v1",
            )

            assert first["created"] is True
            assert first["all_four_forms_in_one_snapshot"]
            assert first["d1_aggregate_evidence_verified"]
            assert first[
                "pit_levy_statutory_components_verified"
            ] is True
            assert first[
                "pit_levy_statutory_evidence_in_snapshot"
            ] is True
            assert first[
                "fourdf_posted_payment_evidence_verified"
            ]
            assert first["d5_d6_hr_inventory_verified"]
            assert first["official_generation"] == "BLOCKED"

            stored = (
                await db.execute(
                    text("""
                        SELECT version, payload_json, snapshot_sha256
                        FROM payroll_regulatory_internal_snapshots
                        WHERE company_id = :cid
                          AND payroll_period_id = :pid
                          AND id = :sid
                    """),
                    {
                        "cid": company.id,
                        "pid": period["id"],
                        "sid": first["id"],
                    },
                )
            ).mappings().one()

            payload = json.loads(stored["payload_json"])
            rows = payload["rows"]

            assert {row["form"] for row in rows} == {
                "D1", "4DF", "D5", "D6"
            }

            evidence_by_form = {}

            for row in rows:
                evidence_by_form.setdefault(
                    row["form"], []
                ).append(row)

            for row in evidence_by_form["D1"]:
                assert deserialize_anchor(
                    row["attributes"][
                        "d1_aggregate_evidence"
                    ]
                )["control"]

                tax = verify_tax_anchor(
                    row["attributes"][
                        "pit_levy_statutory_evidence"
                    ]
                )

                assert tax["calculation"] == calculation.id
                assert tax["company"] == company.id
                assert tax["period"] == period["id"]
                assert (
                    float(tax["pit"]) + float(tax["levy"])
                    == float(tax["withheld"])
                )

            for row in evidence_by_form["4DF"]:
                verified = verify_payment_anchor(
                    row["attributes"][
                        "fourdf_posted_payment_evidence"
                    ]
                )
                assert verified["c"] == calculation.id

            hr_evidence = []

            for form in ("D5", "D6"):
                for row in evidence_by_form[form]:
                    hr_evidence.append(
                        row["attributes"][
                            "hr_inventory_evidence"
                        ]
                    )

            assert len(set(hr_evidence)) == 1
            assert verify_hr_anchor(hr_evidence[0])

            print(
                "D1 + 4DF + D5 + D6 ONE SNAPSHOT = PASS",
                "REAL POSTED FINANCIAL SOURCES = PASS",
                "REAL STATUTORY PAYROLL SOURCES = PASS",
                "REAL HR INVENTORY = PASS",
                "ALL THREE EVIDENCE DIGESTS = PASS",
                "INDEPENDENT PIT/LEVY LINES = PASS",
                "PIT/LEVY IMMUTABLE BINDING = PASS",
                flush=True,
                sep="\n",
            )

            replay = await persist_combined_four_form_review(
                db,
                **args,
                request_key="combined-four-form-v1",
            )

            assert replay["created"] is False
            assert replay["id"] == first["id"]

            next_version = await persist_combined_four_form_review(
                db,
                **args,
                request_key="combined-four-form-v2",
            )

            assert next_version["created"] is True
            assert next_version["version"] == 2

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
                        "sid": next_version["id"],
                    },
                )
            ).scalar_one()

            assert previous == first["id"]

            # Validate both versions with the same evidence
            # re-read verifier, without trusting client rows.
            for snapshot_id in (
                first["id"],
                next_version["id"],
            ):
                await verify_combined_persisted_snapshot(
                    db,
                    company_id=company.id,
                    payroll_period_id=period["id"],
                    payroll_period=label,
                    snapshot_id=snapshot_id,
                    tax_anchors={
                        calculation.id:
                        evidence_by_form["D1"][0][
                            "attributes"
                        ]["pit_levy_statutory_evidence"]
                    },
                    d1_anchor=deserialize_anchor(
                        evidence_by_form["D1"][0][
                            "attributes"
                        ]["d1_aggregate_evidence"]
                    ),
                    payment_anchors={
                        calculation.id:
                        evidence_by_form["4DF"][0][
                            "attributes"
                        ]["fourdf_posted_payment_evidence"]
                    },
                    hr_anchor=hr_evidence[0],
                )

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
                        "second_id": next_version["id"],
                    },
                )
            ).scalar_one()

            assert count == 0

            print(
                "COMBINED EXACT REPLAY = PASS",
                "COMBINED VERSION CHAIN = PASS",
                "COMBINED TRANSACTION ROLLBACK = PASS",
                "OFFICIAL GENERATION = BLOCKED",
                flush=True,
                sep="\n",
            )

    finally:
        await engine.dispose()
