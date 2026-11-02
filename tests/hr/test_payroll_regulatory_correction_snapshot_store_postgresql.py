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


async def test_d1_and_4df_real_correction_control(employee_engine):
    await protect(employee_engine)

    async with AsyncSession(
        employee_engine,
        expire_on_commit=False,
    ) as db:
        fixture, source, original, evidence, args = await prepare(db)
        await install_and_verify_snapshot_migration(db)
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

        await db.rollback()
