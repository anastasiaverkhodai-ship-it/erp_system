import pytest

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from test_employee_foundation import employee_engine
from test_payroll_regulatory_readiness_postgresql import seed

from app.models.payroll_regulatory_report import PayrollRegulatoryReport

from app.services.payroll_regulatory_hr_snapshot_store import (
    HRReviewSnapshotError,
    persist_hr_review_snapshot,
    load_hr_snapshot,
    compare_persisted_hr_snapshots,
)


pytestmark = pytest.mark.asyncio


async def create_report(db, company, user, calculation, kind, revision):
    report = PayrollRegulatoryReport(
        company_id=company.id,
        payroll_period_id=calculation.payroll_period_id,
        report_kind=kind,
        revision=revision,
        status="draft",
        created_by=user.id,
    )

    db.add(report)
    await db.flush()
    return report


@pytest.mark.parametrize("kind", ["d5", "d6"])
async def test_persisted_d5_d6_review_and_revisions(
    employee_engine, kind,
):
    async with AsyncSession(
        employee_engine,
        expire_on_commit=False,
    ) as db:
        company, user, contract, calculation, *_ = await seed(db)

        first_report = await create_report(
            db, company, user, calculation, kind, 1
        )

        first = await persist_hr_review_snapshot(
            db,
            company_id=company.id,
            payroll_regulatory_report_id=first_report.id,
        )

        repeated = await persist_hr_review_snapshot(
            db,
            company_id=company.id,
            payroll_regulatory_report_id=first_report.id,
        )

        assert first["id"] == repeated["id"]
        assert first["sha256"] == repeated["sha256"]

        loaded = await load_hr_snapshot(
            db,
            company_id=company.id,
            snapshot_id=first["id"],
        )

        assert loaded["payload"]["identity"]["report_kind"] == kind
        assert loaded["payload"][
            "official_statutory_generation"
        ] == "BLOCKED"

        second_report = await create_report(
            db, company, user, calculation, kind, 2
        )

        second = await persist_hr_review_snapshot(
            db,
            company_id=company.id,
            payroll_regulatory_report_id=second_report.id,
        )

        assert second["previous_snapshot_id"] == first["id"]

        compared = await compare_persisted_hr_snapshots(
            db,
            company_id=company.id,
            previous_snapshot_id=first["id"],
            current_snapshot_id=second["id"],
        )

        assert compared["change_count"] == 0
        assert compared["statutory_correction_generated"] is False

        await db.rollback()


async def test_cross_company_snapshot_isolation(employee_engine):
    async with AsyncSession(
        employee_engine, expire_on_commit=False
    ) as db:
        company, user, contract, calculation, *_ = await seed(db)

        report = await create_report(
            db, company, user, calculation, "d5", 1
        )

        saved = await persist_hr_review_snapshot(
            db,
            company_id=company.id,
            payroll_regulatory_report_id=report.id,
        )

        with pytest.raises(HRReviewSnapshotError):
            await load_hr_snapshot(
                db,
                company_id=company.id + 100000,
                snapshot_id=saved["id"],
            )

        await db.rollback()


async def test_postgresql_prevents_snapshot_mutation(employee_engine):
    """Prove database enforcement against a visible persisted row."""

    async with AsyncSession(
        employee_engine,
        expire_on_commit=False,
    ) as db:
        company, user, contract, calculation, *_ = await seed(db)

        report = await create_report(
            db, company, user, calculation, "d5", 1
        )

        saved = await persist_hr_review_snapshot(
            db,
            company_id=company.id,
            payroll_regulatory_report_id=report.id,
        )

        snapshot_id = saved["id"]

        # Confirm this row exists in the current transaction.
        count = await db.scalar(
            text("""
                SELECT count(*)
                FROM payroll_hr_review_snapshots
                WHERE id = :snapshot_id
                  AND company_id = :company_id
            """),
            {
                "snapshot_id": snapshot_id,
                "company_id": company.id,
            },
        )

        assert count == 1, (
            "Snapshot row is not visible before mutation test"
        )

        # Confirm the exact expected trigger exists and is active.
        trigger = (
            await db.execute(
                text("""
                    SELECT
                        tgname,
                        tgenabled,
                        pg_get_triggerdef(oid) AS definition
                    FROM pg_trigger
                    WHERE tgrelid =
                        'payroll_hr_review_snapshots'::regclass
                      AND tgname =
                        'trg_payroll_hr_review_snapshot_immutable'
                      AND NOT tgisinternal
                """)
            )
        ).mappings().one_or_none()

        assert trigger is not None, (
            "Append-only database trigger is missing"
        )

        replication_mode = await db.scalar(
            text("SHOW session_replication_role")
        )

        print(
            "POSTGRESQL TRIGGER STATUS =",
            trigger["tgenabled"],
        )
        print(
            "POSTGRESQL SESSION REPLICATION ROLE =",
            replication_mode,
        )
        print(
            "POSTGRESQL TRIGGER DEFINITION =",
            trigger["definition"],
        )

        # PostgreSQL CHAR fields may be returned as bytes.
        trigger_enabled = trigger["tgenabled"]

        if isinstance(trigger_enabled, bytes):
            trigger_enabled = trigger_enabled.decode("ascii")

        assert trigger_enabled in ("O", "A"), (
            "Append-only trigger is disabled or replica-only"
        )

        assert replication_mode in ("origin", "local"), (
            "Session bypasses ordinary PostgreSQL triggers"
        )

        # PostgreSQL can normalize the event order when
        # rendering pg_get_triggerdef().
        import re

        trigger_definition = trigger["definition"].upper()

        assert re.search(
            r"\bBEFORE\s+(?:UPDATE\s+OR\s+DELETE|DELETE\s+OR\s+UPDATE)\b",
            trigger_definition,
        ), "Trigger does not protect both UPDATE and DELETE"

        # UPDATE must target exactly the visible snapshot,
        # and the database must reject the mutation.
        with pytest.raises(DBAPIError):
            async with db.begin_nested():
                await db.execute(
                    text("""
                        UPDATE payroll_hr_review_snapshots
                        SET payload_json = '{}'
                        WHERE id = :snapshot_id
                          AND company_id = :company_id
                    """),
                    {
                        "snapshot_id": snapshot_id,
                        "company_id": company.id,
                    },
                )

        print("POSTGRESQL UPDATE PROTECTION = PASS")

        # DELETE must be rejected independently.
        with pytest.raises(DBAPIError):
            async with db.begin_nested():
                await db.execute(
                    text("""
                        DELETE FROM payroll_hr_review_snapshots
                        WHERE id = :snapshot_id
                          AND company_id = :company_id
                    """),
                    {
                        "snapshot_id": snapshot_id,
                        "company_id": company.id,
                    },
                )

        print("POSTGRESQL DELETE PROTECTION = PASS")

        unchanged = await load_hr_snapshot(
            db,
            company_id=company.id,
            snapshot_id=snapshot_id,
        )

        assert unchanged["sha256"] == saved["sha256"]
        assert unchanged["payload"] == saved["payload"]

        remaining = await db.scalar(
            text("""
                SELECT count(*)
                FROM payroll_hr_review_snapshots
                WHERE id = :snapshot_id
            """),
            {"snapshot_id": snapshot_id},
        )

        assert remaining == 1

        print("POSTGRESQL SNAPSHOT UNCHANGED = PASS")

        await db.rollback()
