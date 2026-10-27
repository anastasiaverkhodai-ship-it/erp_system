"""Correction valuation reuses the production calculator without rewriting history."""
from decimal import Decimal
from uuid import uuid4
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from test_employee_foundation import employee_engine
from test_payroll_secondary_sick_postgresql import prepare
from app.models.payroll_sick_leave import PayrollSickLeaveCalculation, PayrollSickLeaveCalculationSource
from app.models.payroll_insurance_evidence import PayrollInsuranceEvidence
from app.schemas.payroll_insurance_evidence import InsuranceEvidenceCreate
from app.services.payroll_insurance_evidence_service import create_insurance_evidence, review_insurance_evidence
from app.services.payroll_sick_history_service import calculate_sick_from_history
from app.services.payroll_sick_leave_service import PayrollSickLeaveConflictError

pytestmark = pytest.mark.asyncio


async def changed_evidence(db, company, args, *, reviewed=True):
    original = await db.get(PayrollInsuranceEvidence, args['insurance_evidence_id'])
    values = {name: getattr(original, name) for name in InsuranceEvidenceCreate.model_fields}
    values.update(total_service_months=120 if original.total_service_months < 120 else 12,
                  document_reference='Corrected independently reviewed insurance history',
                  request_key=uuid4().hex)
    new = await create_insurance_evidence(db, company_id=company.id,
        data=InsuranceEvidenceCreate(**values), created_by=original.created_by)
    if reviewed:
        await review_insurance_evidence(db, company_id=company.id, evidence_id=new.id,
                                       reviewed_by=original.reviewed_by, approved=True)
    return new


async def test_preview_recalculates_but_preserves_source_and_membership(employee_engine):
    async with AsyncSession(employee_engine, expire_on_commit=False) as db:
        company, _, _, _, args = await prepare(db)
        original = await calculate_sick_from_history(db, **args)
        amount = original.sick_pay_amount
        evidence = await changed_evidence(db, company, args)
        before = [await db.scalar(select(func.count()).select_from(model))
                  for model in (PayrollSickLeaveCalculation, PayrollSickLeaveCalculationSource)]
        replacement, sources = await calculate_sick_from_history(db,
            **dict(args, insurance_evidence_id=evidence.id), _preview_replacement_of=original.id)
        assert replacement.id is None and sources and all(x.id is None for x in sources)
        assert replacement.sick_pay_amount != amount
        assert replacement.employer_amount + replacement.insurer_amount == replacement.sick_pay_amount
        assert replacement.insurance_evidence_id == evidence.id
        assert original.sick_pay_amount == amount
        assert not db.new
        after = [await db.scalar(select(func.count()).select_from(model))
                 for model in (PayrollSickLeaveCalculation, PayrollSickLeaveCalculationSource)]
        assert before == after
        assert (await calculate_sick_from_history(db, **args)).id == original.id


async def test_preview_refuses_another_original(employee_engine):
    async with AsyncSession(employee_engine, expire_on_commit=False) as db:
        _, _, _, _, args = await prepare(db)
        original = await calculate_sick_from_history(db, **args)
        with pytest.raises(PayrollSickLeaveConflictError, match='current sick'):
            await calculate_sick_from_history(db, **args, _preview_replacement_of=original.id + 999)


async def test_preview_requires_verified_evidence(employee_engine):
    from app.services.payroll_insurance_evidence_service import PayrollInsuranceEvidenceError
    async with AsyncSession(employee_engine, expire_on_commit=False) as db:
        company, _, _, _, args = await prepare(db)
        original = await calculate_sick_from_history(db, **args)
        evidence = await changed_evidence(db, company, args, reviewed=False)
        with pytest.raises(PayrollInsuranceEvidenceError, match='Verified'):
            await calculate_sick_from_history(db,
                **dict(args, insurance_evidence_id=evidence.id), _preview_replacement_of=original.id)


async def test_new_version_preserves_old_source_and_selects_current(employee_engine):
    from app.services.payroll_sick_replacement_source_service import materialize_sick_replacement
    from app.services.payroll_sick_leave_service import get_sick_leave_calculation_for_leave
    async with AsyncSession(employee_engine, expire_on_commit=False) as db:
        company, _, _, _, args = await prepare(db)
        original = await calculate_sick_from_history(db, **args)
        original_amount = original.sick_pay_amount
        evidence = await changed_evidence(db, company, args)
        replacement = await materialize_sick_replacement(db, company_id=company.id,
            original_calculation_id=original.id, insurance_evidence_id=evidence.id,
            calculated_by=args['calculated_by'])
        assert replacement.revision == 2 and replacement.supersedes_id == original.id
        assert replacement.id != original.id
        assert original.sick_pay_amount == original_amount
        current = await get_sick_leave_calculation_for_leave(db, company_id=company.id,
                                                           leave_request_id=original.leave_request_id)
        assert current.id == replacement.id
        with pytest.raises(PayrollSickLeaveConflictError, match='current sick'):
            await materialize_sick_replacement(db, company_id=company.id,
                original_calculation_id=original.id, insurance_evidence_id=evidence.id,
                calculated_by=args['calculated_by'])


async def install_revision_guards(engine):
    from importlib.util import spec_from_file_location, module_from_spec
    from pathlib import Path
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    modules = []
    for number in ('61', '62', '63'):
        path = next(Path('alembic/versions').glob(f'13d4a7b8c0{number}_*.py'))
        spec = spec_from_file_location('sick_migration_' + number, path)
        module = module_from_spec(spec)
        spec.loader.exec_module(module)
        modules.append(module)
    async with engine.begin() as conn:
        def install(sync):
            with Operations.context(MigrationContext.configure(sync)):
                # Metadata already supplied the current columns and constraints;
                # install the actual migration functions/triggers on that schema.
                from contextlib import ExitStack
                from unittest.mock import patch
                from alembic import op
                with ExitStack() as patches:
                    for name in ('add_column', 'drop_constraint', 'create_unique_constraint',
                                 'create_foreign_key', 'create_check_constraint', 'create_table'):
                        patches.enter_context(patch.object(op, name))
                    for module in modules:
                        module.upgrade()
        await conn.run_sync(install)


async def test_database_preserves_both_versions_and_rejects_mutation(employee_engine):
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError
    from app.services.payroll_sick_replacement_source_service import materialize_sick_replacement
    await install_revision_guards(employee_engine)
    async with AsyncSession(employee_engine, expire_on_commit=False) as db:
        company, _, _, _, args = await prepare(db)
        original = await calculate_sick_from_history(db, **args)
        evidence = await changed_evidence(db, company, args)
        replacement = await materialize_sick_replacement(db, company_id=company.id,
            original_calculation_id=original.id, insurance_evidence_id=evidence.id,
            calculated_by=args['calculated_by'])
        for row_id in (original.id, replacement.id):
            with pytest.raises(DBAPIError, match='immutable'):
                async with db.begin_nested():
                    await db.execute(text('UPDATE payroll_sick_leave_calculations SET calculated_by=calculated_by WHERE id=:id'), {'id': row_id})
            with pytest.raises(DBAPIError, match='immutable'):
                async with db.begin_nested():
                    await db.execute(text('DELETE FROM payroll_sick_leave_calculations WHERE id=:id'), {'id': row_id})
        assert await db.scalar(select(func.count()).select_from(PayrollSickLeaveCalculation)) == 2


async def test_source_materialization_rolls_back_with_outer_financial_failure(employee_engine):
    from app.services.payroll_sick_replacement_source_service import materialize_sick_replacement
    from app.services.payroll_sick_leave_service import get_sick_leave_calculation_for_leave
    await install_revision_guards(employee_engine)
    async with AsyncSession(employee_engine, expire_on_commit=False) as db:
        company, _, _, _, args = await prepare(db)
        original = await calculate_sick_from_history(db, **args)
        evidence = await changed_evidence(db, company, args)
        with pytest.raises(ValueError, match='financial failure'):
            async with db.begin_nested():
                await materialize_sick_replacement(db, company_id=company.id,
                    original_calculation_id=original.id, insurance_evidence_id=evidence.id,
                    calculated_by=args['calculated_by'])
                raise ValueError('financial failure')
        current = await get_sick_leave_calculation_for_leave(db, company_id=company.id,
            leave_request_id=original.leave_request_id)
        assert current.id == original.id
        assert await db.scalar(select(func.count()).select_from(PayrollSickLeaveCalculation)) == 1
