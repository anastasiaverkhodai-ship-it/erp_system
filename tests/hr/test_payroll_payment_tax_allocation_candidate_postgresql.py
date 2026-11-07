"""Real PostgreSQL candidate persistence and negative checks."""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from test_employee_foundation import employee_engine
from test_payroll_insurance_funding import setup

from app.models.employee import Employee
from app.models.payroll_statutory import (
    PayrollStatutoryResult,
    PayrollStatutoryResultLine,
)
from app.services.payroll_disbursement_service import (
    create_payroll_disbursement,
    generate_and_post_payroll_disbursement_journal,
)
from app.services.payroll_payment_tax_allocation_candidate import (
    PaymentTaxAllocationError,
    create_payment_tax_allocation_candidate,
)


pytestmark = pytest.mark.asyncio


def _install_candidate_migration(sync_connection):
    """Apply the real migration within the test transaction.

    Never touch the source ERP database.
    """
    from alembic.runtime.migration import MigrationContext
    from alembic.operations import Operations
    import runpy

    database = sync_connection.exec_driver_sql(
        "SELECT current_database()"
    ).scalar_one()

    if database == "erp_db":
        raise RuntimeError(
            "STOP: Refusing migration on production ERP DB"
        )

    existing = sync_connection.exec_driver_sql(
        "SELECT to_regclass("
        "'payroll_payment_tax_allocation_candidates'"
        ")"
    ).scalar_one()

    if existing is not None:
        raise RuntimeError(
            "STOP: Test migration table already exists"
        )

    migration = runpy.run_path(
        "alembic/versions/"
        "p13wtalloc261107_payment_tax_allocation_candidates.py"
    )

    if (
        migration["revision"] != "p13wtalloc261107"
        or migration["down_revision"] != "p13d6grant261105"
    ):
        raise RuntimeError(
            "STOP: Unexpected migration lineage"
        )

    context = MigrationContext.configure(sync_connection)

    review_migration = runpy.run_path(
        "alembic/versions/"
        "p13wtrev261107_payment_tax_allocation_events.py"
    )

    if (
        review_migration["revision"] != "p13wtrev261107"
        or review_migration["down_revision"]
            != "p13wtalloc261107"
    ):
        raise RuntimeError(
            "STOP: Invalid review migration lineage"
        )

    with Operations.context(context):
        migration["upgrade"]()
        review_migration["upgrade"]()

    events = sync_connection.exec_driver_sql(
        "SELECT to_regclass("
        "'payroll_payment_tax_allocation_candidate_events'"
        ")"
    ).scalar_one()

    if events is None:
        raise RuntimeError(
            "STOP: Review event table missing after upgrade"
        )

    installed = sync_connection.exec_driver_sql(
        "SELECT to_regclass("
        "'payroll_payment_tax_allocation_candidates'"
        ")"
    ).scalar_one()

    if installed is None:
        schema = sync_connection.exec_driver_sql(
            "SELECT current_schema()"
        ).scalar_one()
        search_path = sync_connection.exec_driver_sql(
            "SHOW search_path"
        ).scalar_one()
        raise RuntimeError(
            "STOP: Candidate migration table missing; "
            f"current_schema={schema}; "
            f"search_path={search_path}"
        )


async def _initialize_disposable_schema(db):
    connection = await db.connection()
    await connection.run_sync(
        _install_candidate_migration
    )


async def prepare_payment(db):
    await _initialize_disposable_schema(db)
    (
        company, actor, contract, calc,
        accrual, accounts, bank
    ) = await setup(db)

    employee = await db.get(Employee, contract.employee_id)
    employee.payment_iban = "UA213223130000026007233566001"
    await db.flush()

    statutory = await db.scalar(
        select(PayrollStatutoryResult).where(
            PayrollStatutoryResult.company_id == company.id,
            PayrollStatutoryResult.payroll_calculation_id == calc.id,
        )
    )
    assert statutory is not None

    pit = await db.scalar(
        select(PayrollStatutoryResultLine).where(
            PayrollStatutoryResultLine.company_id == company.id,
            PayrollStatutoryResultLine.payroll_statutory_result_id
                == statutory.id,
            PayrollStatutoryResultLine.component
                == "personal_income_tax",
        )
    )
    assert pit is not None and pit.amount > 0

    amount = (Decimal(statutory.net_amount) / 2).quantize(
        Decimal("0.01")
    )
    assert amount > 0

    disbursement = await create_payroll_disbursement(
        db,
        company_id=company.id,
        payroll_calculation_id=calc.id,
        bank_account_id=bank.id,
        cash_desk_id=None,
        payment_date=date(2026, 9, 30),
        created_by=actor.id,
        amount=amount,
        request_key="candidate-real-payment",
    )

    await generate_and_post_payroll_disbursement_journal(
        db,
        company_id=company.id,
        payroll_disbursement_id=disbursement.id,
        created_by=actor.id,
    )

    kwargs = dict(
        company_id=company.id,
        payroll_calculation_id=calc.id,
        payroll_disbursement_id=disbursement.id,
        statutory_line_id=pit.id,
        amount=Decimal("0.01"),
        request_key="candidate-tax-source",
        evidence_reference="TEST_DRAFT_ONLY",
        created_by=actor.id,
    )
    return kwargs


async def test_pg_persisted_tax_candidate(employee_engine):
    async with AsyncSession(
        employee_engine, expire_on_commit=False
    ) as db:
        kwargs = await prepare_payment(db)

        result = await create_payment_tax_allocation_candidate(
            db, **kwargs
        )
        assert result["status"] == "draft"
        assert not result["payment_specific_withholding_verified"]
        assert result["official_generation"] == "BLOCKED"

        same = await create_payment_tax_allocation_candidate(
            db, **kwargs
        )
        assert same["id"] == result["id"]

        stored = await db.execute(
            text("""
                SELECT component, amount, status
                FROM payroll_payment_tax_allocation_candidates
                WHERE id=:id AND company_id=:c
            """),
            {"id": result["id"], "c": kwargs["company_id"]},
        )
        row = stored.one()
        assert row.component == "personal_income_tax"
        assert row.amount == Decimal("0.01")
        assert row.status == "draft"

        await db.rollback()


async def test_pg_wrong_identity_and_duplicate_blocked(
    employee_engine,
):
    async with AsyncSession(
        employee_engine, expire_on_commit=False
    ) as db:
        kwargs = await prepare_payment(db)

        await create_payment_tax_allocation_candidate(
            db, **kwargs
        )

        wrong = dict(kwargs)
        wrong["company_id"] += 999999

        with pytest.raises(PaymentTaxAllocationError):
            await create_payment_tax_allocation_candidate(
                db, **wrong
            )

        conflicting = dict(kwargs)
        conflicting["amount"] = Decimal("0.02")

        with pytest.raises(
            PaymentTaxAllocationError,
            match="Idempotency",
        ):
            await create_payment_tax_allocation_candidate(
                db, **conflicting
            )

        duplicate = dict(kwargs)
        duplicate["request_key"] = "different-request"

        with pytest.raises(
            PaymentTaxAllocationError,
            match="Duplicate",
        ):
            await create_payment_tax_allocation_candidate(
                db, **duplicate
            )

        await db.rollback()
