"""Integrated D1/4DF financial control using actual correction records."""

import os
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from test_employee_foundation import employee_engine
from test_payroll_correction_basis_postgresql import prepare, protect

from app.models.payroll import PayrollPeriod
from app.models.payroll_statutory import PayrollStatutoryComponent

from app.services.payroll_statutory_service import (
    create_statutory_rate,
)
from app.services.payroll_dated_employee_statutory import (
    write_dated_employee_statutory_result,
)
from app.services.payroll_correction_recalculation_service import (
    recalculate_minimum_wage_correction,
)
from app.services.payroll_regulatory_correction_report_control import (
    review_persisted_correction_report,
)
from decimal import Decimal

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        os.getenv("RUN_POSTGRES_E2E") != "1",
        reason="Real PostgreSQL required",
    ),
]



async def install_and_verify_snapshot_migration(db):
    """Verify the migrated public table from the actual payroll session.

    No test-time schema migration, no search_path modification,
    no source database writes.
    """
    from sqlalchemy import text

    conn = await db.connection()

    evidence = (
        await conn.execute(
            text("""
                SELECT
                    current_database() AS database_name,
                    current_schema() AS schema_name,
                    current_setting('search_path') AS search_path,
                    to_regclass(
                        'public.payroll_regulatory_correction_snapshots'
                    ) AS snapshot_table
            """)
        )
    ).mappings().one()

    database = evidence["database_name"]

    assert database.startswith("erp_r3_copy_"), (
        "STOP: PostgreSQL copy isolation missing"
    )

    assert evidence["snapshot_table"] is not None, (
        "Alembic snapshot migration was not applied"
    )

    trigger_count = await conn.scalar(
        text("""
            SELECT count(*)
            FROM pg_trigger
            WHERE tgrelid = to_regclass(
                'public.payroll_regulatory_correction_snapshots'
            )
              AND tgname = 'trg_prcs_immutable'
              AND tgenabled = 'O'
              AND NOT tgisinternal
        """)
    )

    assert trigger_count == 1, (
        "Immutable snapshot trigger missing or disabled"
    )

    columns = (
        await conn.execute(
            text("""
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name =
                    'payroll_regulatory_correction_snapshots'
            """)
        )
    ).scalars().all()

    expected_columns = {
        'id',
        'company_id',
        'payroll_period_id',
        'report_kind',
        'original_calculation_id',
        'replacement_calculation_id',
        'schema_version',
        'projection',
        'projection_sha256',
        'created_at',
    }

    assert set(columns) == expected_columns, (
        "Snapshot migration columns mismatch"
    )

    print("SESSION DATABASE =", database)
    print("SESSION SCHEMA =", evidence["schema_name"])
    print("SESSION SEARCH PATH =", evidence["search_path"])
    print("PUBLIC SNAPSHOT TABLE = VERIFIED")
    print("PUBLIC IMMUTABLE TRIGGER = VERIFIED")
    print("PUBLIC SNAPSHOT SCHEMA = VERIFIED")


@pytest.mark.parametrize("next_norm", [8800, 11000])
async def test_multirevision_delta_and_persisted_history(employee_engine, next_norm):
    await protect(employee_engine)

    async with AsyncSession(
        employee_engine,
        expire_on_commit=False,
    ) as db:
        await install_and_verify_snapshot_migration(db)
        # Sources live in a fresh schema, but committed snapshots live in
        # public. Reserve a company namespace above all existing snapshots
        # before creating sources; never delete or rewrite immutable evidence.
        from sqlalchemy import text
        await db.execute(text("SELECT pg_advisory_xact_lock(130018, 1)"))
        company_start = await db.scalar(text(
            "SELECT COALESCE(MAX(company_id), 0) + 1000000 "
            "FROM public.payroll_regulatory_correction_snapshots"
        ))
        await db.execute(text(
            "SELECT setval(pg_get_serial_sequence('companies', 'id'), "
            ":start, false)"
        ), {"start": company_start})
        fixture, source, original, evidence, args = await prepare(db)
        assert fixture["company"] == company_start
        assert await db.scalar(text(
            "SELECT count(*) FROM public.payroll_regulatory_correction_snapshots "
            "WHERE company_id = :company"
        ), {"company": fixture["company"]}) == 0
        company_id = fixture["company"]
        actor_id = fixture["actor"]

        period = await db.get(
            PayrollPeriod, original.payroll_period_id
        )

        for component, rate in (
            (PayrollStatutoryComponent.PERSONAL_INCOME_TAX, Decimal(".18")),
            (PayrollStatutoryComponent.MILITARY_LEVY, Decimal(".05")),
            (PayrollStatutoryComponent.UNIFIED_SOCIAL_CONTRIBUTION, Decimal(".22")),
        ):
            await create_statutory_rate(
                db,
                company_id=company_id,
                component=component,
                rate=rate,
                effective_from=period.start_date,
                effective_to=None,
                actor_user_id=actor_id,
            )

        await db.flush()

        original_result = await write_dated_employee_statutory_result(
            db,
            company_id=company_id,
            payroll_calculation_id=original.id,
            actor_user_id=actor_id,
        )

        replacement, correction = (
            await recalculate_minimum_wage_correction(
                db, **args
            )
        )

        replacement_result = await write_dated_employee_statutory_result(
            db,
            company_id=company_id,
            payroll_calculation_id=replacement.id,
            actor_user_id=actor_id,
        )

        assert original_result.id != replacement_result.id
        assert replacement.revision == 2
        assert original.gross_amount == Decimal("4323.50")
        assert replacement.gross_amount == Decimal("4755.85")

        for report_kind in ("d1", "4df"):
            result = await review_persisted_correction_report(
                db,
                company_id=company_id,
                payroll_period_id=period.id,
                report_kind=report_kind,
            )

            print(
                report_kind.upper(),
                "CORRECTION FINANCIAL STATUS =",
                result["financial_status"],
            )

            assert result["financial_status"] == (
                "REPORT_CORRECTION_FINANCIALS_RECONCILED"
            ), result

            assert result["financial_correction_verified"] is True
            assert result["active_calculation_ids"] == [replacement.id]
            assert result["excluded_historical_calculation_ids"] == [
                original.id
            ]
            assert result["official_form_fields_verified"] is False
            assert result["official_generation"] == "BLOCKED"

            from app.services.payroll_regulatory_correction_projection import (
                review_and_seal_persisted_correction,
                verify_projection_seal,
            )

            sealed = await review_and_seal_persisted_correction(
                db,
                company_id=company_id,
                payroll_period_id=period.id,
                report_kind=report_kind,
            )
            assert sealed["status"] == (
                "INTERNAL_FINANCIAL_PROJECTION_VERIFIED"
            ), sealed
            assert sealed["database_snapshot_persisted"] is False
            assert sealed["official_generation"] == "BLOCKED"
            assert verify_projection_seal(sealed["seal"])
            payload = sealed["seal"]["payload"]
            assert payload["active_calculation_ids"] == [replacement.id]
            assert payload[
                "excluded_historical_calculation_ids"
            ] == [original.id]
            assert len(payload["components"]) == 3
            assert payload["report_kind"] == report_kind
            print(
                report_kind.upper(),
                "SOURCE-BOUND PROJECTION = PASS",
            )

            from sqlalchemy import text
            from sqlalchemy.exc import DBAPIError

            from app.services.payroll_regulatory_correction_snapshot_store import (
                persist_correction_snapshot,
            )

            first = await persist_correction_snapshot(
                db,
                company_id=company_id,
                payroll_period_id=period.id,
                report_kind=report_kind,
            )

            again = await persist_correction_snapshot(
                db,
                company_id=company_id,
                payroll_period_id=period.id,
                report_kind=report_kind,
            )

            assert first["status"] == (
                "PERSISTED_INTERNAL_SNAPSHOT_VERIFIED"
            )
            assert first["created"] is True
            assert again["created"] is False
            assert first["id"] == again["id"]
            assert first["projection_sha256"] == (
                again["projection_sha256"]
            )
            assert first["database_snapshot_persisted"] is True
            assert first["owner_role_separation_verified"] is False
            assert first["official_generation"] == "BLOCKED"

            for sql in (
                "UPDATE public.payroll_regulatory_correction_snapshots "
                "SET projection_sha256 = projection_sha256 "
                "WHERE id = :snapshot_id",
                "DELETE FROM public.payroll_regulatory_correction_snapshots "
                "WHERE id = :snapshot_id",
            ):
                with pytest.raises(DBAPIError):
                    async with db.begin_nested():
                        await db.execute(
                            text(sql),
                            {"snapshot_id": first["id"]},
                        )

            print(
                report_kind.upper(),
                "IMMUTABLE POSTGRESQL SNAPSHOT = PASS",
            )



        print("REAL D1/4DF CORRECTION FINANCIAL CONTROL = PASS")
        print("REAL ORIGINAL AND REPLACEMENT SOURCES = PASS")
        print("REAL HISTORICAL DOUBLE COUNT CONTROL = PASS")

        # Snapshot rows are committed and read back, never inferred from a
        # domain return value. Concurrent identical requests create one v3.
        import asyncio
        from sqlalchemy import select
        from app.models.payroll import PayrollCalculation
        from app.services.payroll_correction_source_evidence_service import capture_payroll_correction_source
        from app.services.payroll_regulatory_persisted_correction_delta import review_persisted_correction_delta
        from app.services.payroll_regulatory_correction_snapshot_store import CorrectionSnapshotError
        from app.services.payroll_correction_recalculation_contract import PayrollCorrectionRecalculationError

        query = text("SELECT id, projection, projection_sha256 FROM public.payroll_regulatory_correction_snapshots WHERE company_id=:company ORDER BY id")
        snapshots_before = (await db.execute(query, {"company": company_id})).mappings().all()
        assert len(snapshots_before) == 2
        replacement_id = replacement.id
        replacement_gross = replacement.gross_amount
        next_args = dict(args, original_payroll_calculation_id=replacement_id,
            request_key="correction-2", reason="Second calendar correction",
            corrected_minimum_wage_context=dict(args["corrected_minimum_wage_context"],
                full_time_norm_minutes=next_norm, norm_reference="Second calendar"))
        evidence = await capture_payroll_correction_source(db, company_id=company_id,
            original_payroll_calculation_id=replacement_id,
            request_key=next_args["request_key"], reason=next_args["reason"])
        next_args["expected_source_fingerprint_sha256"] = evidence["source_fingerprint_sha256"]
        await db.commit()

        async def create_next():
            async with AsyncSession(employee_engine, expire_on_commit=False) as writer:
                third, link = await recalculate_minimum_wage_correction(writer, **next_args)
                await writer.commit()
                return third.id, link.id

        results = await asyncio.gather(create_next(), create_next())
        assert results[0] == results[1]
        third = await db.get(PayrollCalculation, results[0][0])
        assert third.revision == 3
        await write_dated_employee_statutory_result(db, company_id=company_id,
            payroll_calculation_id=third.id, actor_user_id=actor_id)
        delta = await review_persisted_correction_delta(db, company_id=company_id,
            payroll_period_id=period.id)
        assert delta["persisted_correction_delta_verified"] is True
        assert delta["baseline_calculation_ids"] == [replacement_id]
        pit = next(row for row in delta["components"] if row["component"] == "personal_income_tax")
        assert Decimal(pit["original_base"]) == replacement_gross
        assert Decimal(pit["replacement_base"]) == third.gross_amount
        assert Decimal(pit["base_delta"]) == third.gross_amount - replacement_gross
        assert (Decimal(pit["base_delta"]) > 0) == (next_norm < 9600)
        with pytest.raises(PayrollCorrectionRecalculationError, match="different correction"):
            await recalculate_minimum_wage_correction(db, **dict(next_args,
                expected_source_fingerprint_sha256="0" * 64))
        for report_kind in ("d1", "4df"):
            created = await persist_correction_snapshot(db, company_id=company_id,
                payroll_period_id=period.id, report_kind=report_kind)
            assert created["created"] is True
            assert created["official_generation"] == "BLOCKED"
            for verb in ("UPDATE", "DELETE"):
                sql = ("UPDATE public.payroll_regulatory_correction_snapshots "
                       "SET projection_sha256 = projection_sha256 WHERE id=:id"
                       if verb == "UPDATE" else
                       "DELETE FROM public.payroll_regulatory_correction_snapshots WHERE id=:id")
                with pytest.raises(DBAPIError):
                    async with db.begin_nested():
                        await db.execute(text(sql), {"id": created["id"]})
        await db.commit()

        async def retry_snapshot(report_kind):
            async with AsyncSession(employee_engine) as writer:
                result = await persist_correction_snapshot(writer, company_id=company_id,
                    payroll_period_id=period.id, report_kind=report_kind)
                await writer.commit()
                return result
        for report_kind in ("d1", "4df"):
            retries = await asyncio.gather(retry_snapshot(report_kind), retry_snapshot(report_kind))
            assert retries[0]["id"] == retries[1]["id"]
            assert all(item["created"] is False for item in retries)
        async with AsyncSession(employee_engine) as reader:
            snapshots_after = (await reader.execute(query, {"company": company_id})).mappings().all()
            assert len(snapshots_after) == 4
            assert snapshots_after[:2] == snapshots_before
            for row in snapshots_after[2:]:
                payload = row["projection"]
                assert payload["schema"] == "payroll_internal_correction_projection_v2"
                assert payload["baseline_calculation_ids"] == [replacement_id]
                assert payload["baseline_semantics"] == "immediate_predecessor"
                assert payload["active_calculation_ids"] == [third.id]
                assert payload["excluded_historical_calculation_ids"] == [original.id, replacement_id]
                pit_snapshot = next(c for c in payload["components"] if c["component"] == "personal_income_tax")
                assert Decimal(pit_snapshot["base_delta"]) == third.gross_amount - replacement_gross
            for row in snapshots_after:
                import hashlib
                from app.services.payroll_regulatory_correction_projection import canonical_bytes
                assert hashlib.sha256(canonical_bytes(row["projection"])).hexdigest() == row["projection_sha256"]
        print("MULTIREVISION DELTA, CONCURRENT RETRY, COMMITTED SNAPSHOT HISTORY = PASS")
