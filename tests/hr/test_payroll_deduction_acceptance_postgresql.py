"""C3 candidate evidence/aggregate acceptance on isolated PostgreSQL only."""

import importlib.util
import os
from datetime import date, datetime, timezone
from decimal import Decimal as D
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import Base
from app.models.payroll_deduction import PayrollDeductionInstruction
from app.models.payroll_deduction_legal_evidence import PayrollDeductionLegalEvidence
from app.services.payroll_deduction_aggregate_safety import (
    DeductionAggregateItem, PayrollAggregateSafetyError, assess_payroll_deduction_aggregate,
)
from app.services.payroll_deduction_evidence_write_service import (
    create_pending_deduction_evidence, PayrollDeductionEvidenceWriteError,
)
from app.services.payroll_deduction_evidence_verification_service import (
    verify_deduction_evidence, PayrollDeductionEvidenceVerificationError,
)
from test_employee_foundation import employee_engine
from test_payroll_deduction_postgresql import _seed_identity

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_E2E") != "1", reason="Isolated PostgreSQL required")]
DAY = date(2026, 10, 12)


async def add_instruction(db, company, actor, contract):
    row = PayrollDeductionInstruction(company_id=company.id, employment_contract_id=contract.id,
        deduction_type="candidate", method="fixed", fixed_amount=D("1000"),
        currency_code="UAH", priority=100, effective_from=DAY,
        request_key=uuid4().hex, created_by=actor.id, created_at=datetime.now(timezone.utc))
    db.add(row)
    await db.flush()
    return row


async def pending(db, company, actor, instruction, category="enforcement_other"):
    return await create_pending_deduction_evidence(db=db, company_id=company.id,
        payroll_deduction_instruction_id=instruction.id, legal_category=category,
        document_reference="Acceptance order 123", created_by=actor.id)


async def verify(db, company, actor, row):
    return await verify_deduction_evidence(db=db, company_id=company.id,
        evidence_id=row.id, verified_by=actor.id)


async def assess(db, company, contract, items):
    return await assess_payroll_deduction_aggregate(db, company_id=company.id,
        employment_contract_id=contract.id, effective_date=DAY, currency_code="UAH",
        statutory_net=D("10000"), items=tuple(items))


async def test_persisted_evidence_whole_batch_and_company_isolation(employee_engine):
    async with AsyncSession(employee_engine, expire_on_commit=False) as db:
        company, actor, _, contract = await _seed_identity(db)
        foreign, other_actor, _, other_contract = await _seed_identity(db)
        rows = [await add_instruction(db, company, actor, contract) for _ in range(3)]
        entries = [DeductionAggregateItem(row.id, D("1000"), D("1000")) for row in rows[:2]]
        entries.append(DeductionAggregateItem(rows[2].id, D("1000")))
        with pytest.raises(PayrollAggregateSafetyError, match="required"):
            await assess(db, company, contract, entries)
        for instruction in rows:
            row = await pending(db, company, actor, instruction)
            with pytest.raises(PayrollAggregateSafetyError, match="not verified"):
                await assess(db, company, contract, entries)
            await verify(db, company, actor, row)
        result = await assess(db, company, contract, entries)
        assert result.combined_total == D("5000")
        assert result.remaining_capacity == 0 and result.authorizes_payroll is False
        entries[-1] = DeductionAggregateItem(rows[2].id, D("1000.01"))
        with pytest.raises(PayrollAggregateSafetyError, match="aggregate"):
            await assess(db, company, contract, entries)
        with pytest.raises(PayrollAggregateSafetyError, match="scope"):
            await assess(db, foreign, other_contract, entries)
        with pytest.raises(PayrollAggregateSafetyError, match="scope"):
            await assess(db, company, other_contract, entries)
        with pytest.raises(PayrollDeductionEvidenceWriteError, match="not found"):
            await pending(db, foreign, other_actor, rows[0])
        with pytest.raises(PayrollDeductionEvidenceWriteError, match="already exists"):
            await pending(db, company, actor, rows[0])
        # The database, independently of services, rejects cross-company evidence.
        async with db.begin_nested() as savepoint:
            db.add(PayrollDeductionLegalEvidence(company_id=foreign.id,
                payroll_deduction_instruction_id=rows[0].id, legal_category="enforcement_other",
                document_reference="bad tenant", verification_status="pending",
                created_by=other_actor.id, created_at=datetime.now(timezone.utc)))
            with pytest.raises(IntegrityError):
                await db.flush()
            await savepoint.rollback()
        await db.rollback()


async def test_mixed_verified_categories_and_no_autoflush(employee_engine):
    async with AsyncSession(employee_engine, expire_on_commit=False) as db:
        company, actor, _, contract = await _seed_identity(db)
        rows = [await add_instruction(db, company, actor, contract) for _ in range(2)]
        for row, category in zip(rows, ("enforcement_other", "enforcement_special")):
            await verify(db, company, actor, await pending(db, company, actor, row, category))
        entries = [DeductionAggregateItem(row.id, D("100")) for row in rows]
        # If assessment autoflushes, this invalid pending row fails before its
        # intended mixed-category check. It must remain pending and untouched.
        unsaved = PayrollDeductionInstruction()
        db.add(unsaved)
        with pytest.raises(PayrollAggregateSafetyError, match="Mixed"):
            await assess(db, company, contract, entries)
        assert unsaved in db.new and unsaved.id is None
        await db.rollback()


async def test_cached_pending_evidence_cannot_be_verified_twice(employee_engine):
    async with AsyncSession(employee_engine, expire_on_commit=False) as cached:
        company, actor, _, contract = await _seed_identity(cached)
        row = await pending(cached, company, actor, await add_instruction(cached, company, actor, contract))
        company_id, evidence_id, actor_id = company.id, row.id, actor.id
        await cached.commit()
        async with AsyncSession(employee_engine) as other:
            reviewed = await verify_deduction_evidence(db=other, company_id=company_id,
                evidence_id=evidence_id, verified_by=actor_id)
            reviewed_at = reviewed.verified_at
            await other.commit()
        assert row.verification_status == "pending"
        with pytest.raises(PayrollDeductionEvidenceVerificationError, match="Only pending"):
            await verify_deduction_evidence(db=cached, company_id=company_id,
                evidence_id=evidence_id, verified_by=actor_id)
        assert row.verification_status == "verified"
        assert row.verified_at == reviewed_at
        await cached.rollback()


async def test_migration_roundtrip_matches_model_and_preserves_history(employee_engine):
    spec = importlib.util.spec_from_file_location("deduction_evidence_migration", Path(
        "alembic/versions/13d4a7b8c021_payroll_deduction_legal_evidence.py"))
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    async with employee_engine.begin() as connection:
        def roundtrip(sync):
            with Operations.context(MigrationContext.configure(sync)):
                migration.downgrade()
                migration.upgrade()
            assert compare_metadata(MigrationContext.configure(sync), Base.metadata) == []
        await connection.run_sync(roundtrip)
    async with AsyncSession(employee_engine) as db:
        company, actor, _, contract = await _seed_identity(db)
        await pending(db, company, actor, await add_instruction(db, company, actor, contract))
        def downgrade(sync):
            with Operations.context(MigrationContext.configure(sync)):
                migration.downgrade()
        with pytest.raises(RuntimeError, match="historical records"):
            await (await db.connection()).run_sync(downgrade)
        assert await db.scalar(select(func.count(PayrollDeductionLegalEvidence.id))) == 1
        await db.rollback()
