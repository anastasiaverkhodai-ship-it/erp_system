import os
import ast
from pathlib import Path
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from calendar import monthrange
from datetime import date
from decimal import Decimal as D
import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from test_employee_foundation import employee_engine
from test_payroll_vacation_postgresql import _seed_identity
from test_payroll_regulatory_reporting_postgresql import _payroll_input_values
from app.models.payroll import PayrollPeriod,PayrollInput
from app.models.employment_event import EmploymentEvent
from app.models.payroll_opening import PayrollEarningsHistory
from app.schemas.payroll_leave_balance import LeaveAccountCreate,LeaveAccrualCreate
from app.schemas.payroll_leave_compensation import LeaveCompensationCreate
from app.services.payroll_leave_balance_service import create_leave_account,accrue_leave_days,leave_balances
from app.services.payroll_leave_compensation_service import create_leave_compensation,derive_compensation_payroll_lines

pytestmark=[pytest.mark.asyncio,pytest.mark.skipif(os.getenv('RUN_POSTGRES_E2E')!='1',reason='Isolated PostgreSQL')]


@pytest.mark.parametrize('tamper', [None, 'total', 'days', 'bucket'])
async def test_termination_consumes_rights_and_produces_attributed_payroll_lines(employee_engine, tamper):
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        migration = Path('alembic/versions/13d4a7b8c034_leave_compensation_provenance.py')
        tree = ast.parse(migration.read_text())
        reconcile_sql = next(node.args[0].value for node in ast.walk(tree)
            if isinstance(node, ast.Call) and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
            and 'CREATE FUNCTION payroll_validate_leave_compensation()' in node.args[0].value)
        await db.execute(text(reconcile_sql))
        reversal_tree = ast.parse(Path('alembic/versions/13d4a7b8c035_leave_compensation_reversals.py').read_text())
        upgrade = next(n for n in reversal_tree.body if isinstance(n, ast.FunctionDef) and n.name == 'upgrade')
        for node in ast.walk(upgrade):
            if (isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str) and node.args[0].value.strip().startswith('CREATE')):
                await db.execute(text(node.args[0].value))
        for table in ('payroll_leave_compensations', 'payroll_leave_compensation_lines'):
            await db.execute(text(f'CREATE CONSTRAINT TRIGGER trg_test_{table} AFTER INSERT ON {table} '
                'DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION payroll_validate_leave_compensation()'))
        if tamper is None:
            for table in ('payroll_leave_compensations', 'payroll_leave_compensation_lines'):
                await db.execute(text(f'CREATE TRIGGER trg_test_immutable_{table} BEFORE INSERT OR UPDATE OR DELETE '
                    f'ON {table} FOR EACH ROW EXECUTE FUNCTION payroll_guard_leave_compensation()'))
                await db.execute(text(f'CREATE TRIGGER trg_test_truncate_{table} BEFORE TRUNCATE '
                    f'ON {table} FOR EACH STATEMENT EXECUTE FUNCTION payroll_guard_leave_compensation()'))
        company,user,employee,contract=await _seed_identity(db)
        contract.start_date=date(2025,1,1)
        contract.end_date=date(2026,9,30);contract.status='ended';await db.flush()
        event=EmploymentEvent(company_id=company.id,employment_contract_id=contract.id,event_type='termination',
            effective_date=date(2026,9,30),order_number='TERM-1',order_date=date(2026,9,30),reason='Documented termination',
            request_key='term',before_state={},after_state={},request_payload={},created_by=user.id)
        period=PayrollPeriod(company_id=company.id,year=2026,month=9,start_date=date(2026,9,1),
            end_date=date(2026,9,30),status='draft',created_by=user.id)
        db.add_all([event,period]);await db.flush()
        payroll_input=PayrollInput(**_payroll_input_values(company_id=company.id,period_id=period.id,contract_id=contract.id,actor_id=user.id))
        db.add(payroll_input)
        for i in range(12):
            month=date(2025+(8+i)//12,(8+i)%12+1,1);days=monthrange(month.year,month.month)[1]
            db.add(PayrollEarningsHistory(company_id=company.id,employment_contract_id=contract.id,
                month=month,cutover_date=date(2026,9,1),gross_amount=D(days)*100,
                vacation_earnings=D(days)*100,sick_earnings=D(days)*100,vacation_days=days,sick_days=days,
                source_reference='Historical payroll register',request_fingerprint=str(i),created_by=user.id))
        await db.flush()
        account=await create_leave_account(db,company_id=company.id,actor_id=user.id,data=LeaveAccountCreate(
            employment_contract_id=contract.id,leave_kind='annual',entitlement_code='basic',
            period_start=date(2026,1,1),period_end=date(2026,12,31),
            segments=[dict(start=date(2026,1,1),end=date(2026,12,31),annual_days=24)],
            source_reference='Employment allowance',request_key='annual'))
        await accrue_leave_days(db,company_id=company.id,account_id=account.id,actor_id=user.id,
            data=LeaveAccrualCreate(through=date(2026,9,30),source_reference='Final eligible service',request_key='accrue'))
        data=LeaveCompensationCreate(termination_event_id=event.id,payroll_input_id=payroll_input.id,
            request_key='compensation',entitlement_review_reference='All prior rights used; current right reviewed')
        compensation=await create_leave_compensation(db,company_id=company.id,data=data,actor_id=user.id)
        assert compensation.gross_amount==D(1800)
        assert (await create_leave_compensation(db,company_id=company.id,data=data,actor_id=user.id)).id==compensation.id
        balances=await leave_balances(db,company_id=company.id,employment_contract_id=contract.id,as_of=date(2026,9,30))
        assert balances[0]['from_2024']==0
        lines=await derive_compensation_payroll_lines(db,company_id=company.id,payroll_input=payroll_input,period=period)
        assert len(lines)==1 and lines[0]['amount']==D(1800) and lines[0]['quantity']==18
        assert lines[0]['source_leave_compensation_line_id']>0

        from app.services.employment_event_service import reverse_employment_event
        from app.services.employment_structure_service import EmploymentStructureLifecycleError
        from app.schemas.employment_event import EmploymentEventReverse
        with pytest.raises(EmploymentStructureLifecycleError, match='dependent leave compensation'):
            await reverse_employment_event(db, company_id=company.id, contract_id=contract.id,
                event_id=event.id, created_by=user.id, data=EmploymentEventReverse(
                    order_number='REVERSE', order_date=date(2026,9,30), reason='Correction', request_key='reverse'))
        if tamper == 'total':
            await db.execute(text('UPDATE payroll_leave_compensations SET gross_amount=gross_amount+1 WHERE id=:id'), {'id':compensation.id})
        elif tamper == 'days':
            await db.execute(text('UPDATE payroll_leave_compensation_lines SET consumed_days=consumed_days+1 WHERE compensation_id=:id'), {'id':compensation.id})
        elif tamper == 'bucket':
            await db.execute(text("UPDATE payroll_leave_compensation_lines SET acquisition_bucket='before_2024' WHERE compensation_id=:id"), {'id':compensation.id})
        if tamper:
            with pytest.raises(DBAPIError, match='Leave compensation'):
                await db.execute(text('SET CONSTRAINTS ALL IMMEDIATE'))
            await db.rollback()
        else:
            await db.execute(text('SET CONSTRAINTS ALL IMMEDIATE'))
            await db.commit()

            for statement in (
                'UPDATE payroll_leave_compensations SET gross_amount=0 WHERE id=:id',
                "UPDATE payroll_leave_compensations SET recovery_exemption='{}'::json WHERE id=:id",
                'DELETE FROM payroll_leave_compensations WHERE id=:id',
                'TRUNCATE payroll_leave_compensation_lines CASCADE',
            ):
                with pytest.raises(DBAPIError, match='immutable'):
                    async with db.begin_nested():
                        await db.execute(text(statement), {'id':compensation.id})
            from app.schemas.payroll_leave_compensation import LeaveCompensationReverse
            from app.services.payroll_leave_compensation_service import reverse_leave_compensation
            reversal_data = LeaveCompensationReverse(effective_date=date(2026,9,30),
                source_reference='Correct final settlement', request_key='cancel-compensation')
            reversal = await reverse_leave_compensation(db, company_id=company.id,
                compensation_id=compensation.id, data=reversal_data, actor_id=user.id)
            assert (await reverse_leave_compensation(db, company_id=company.id,
                compensation_id=compensation.id, data=reversal_data, actor_id=user.id)).id == reversal.id
            assert await derive_compensation_payroll_lines(db, company_id=company.id,
                payroll_input=payroll_input, period=period) == []
            restored = await leave_balances(db, company_id=company.id,
                employment_contract_id=contract.id, as_of=date(2026,9,30))
            assert restored[0]['from_2024'] > 17
            replacement = await create_leave_compensation(db, company_id=company.id,
                data=data.model_copy(update={'request_key':'replacement'}), actor_id=user.id)
            assert replacement.id != compensation.id and replacement.gross_amount == D(1800)
            active_lines = await derive_compensation_payroll_lines(db, company_id=company.id,
                payroll_input=payroll_input, period=period)
            assert len(active_lines) == 1 and active_lines[0]['amount'] == D(1800)
            await db.execute(text('SET CONSTRAINTS ALL IMMEDIATE'))
            await db.commit()


@pytest.mark.parametrize('bucket,short_service', [('from_2024', True), ('from_2024', False), ('before_2024', False)])
async def test_compensation_salary_substitution_with_confirmed_zero_history(employee_engine, bucket, short_service):
    from app.models.employee_salary_rate import EmployeeSalaryRate
    from app.schemas.payroll_leave_compensation import CompensationSalarySubstitution
    from app.services.payroll_leave_compensation_basis_service import derive_compensation_average
    from app.services.payroll_average_history_service import PayrollAverageHistoryError
    async with AsyncSession(employee_engine, expire_on_commit=False) as db:
        company,user,employee,contract=await _seed_identity(db)
        contract.start_date=date(2026,9,1) if short_service else date(2022,1,1)
        db.add(EmployeeSalaryRate(company_id=company.id, employment_contract_id=contract.id,
            rate_type='monthly', amount=D(3000), currency_code='UAH', effective_from=contract.start_date))
        if not short_service:
            for i in range(12):
                month = date(2023, i+1, 1) if bucket == 'before_2024' else date(2025+(8+i)//12,(8+i)%12+1,1)
                db.add(PayrollEarningsHistory(company_id=company.id, employment_contract_id=contract.id,
                    month=month, cutover_date=date(2026,9,1), gross_amount=0,
                    vacation_earnings=0, sick_earnings=0, vacation_days=0, sick_days=0,
                    source_reference='Confirmed unpaid absence month', request_fingerprint=str(i), created_by=user.id))
        await db.flush()
        args=dict(company_id=company.id, employment_contract_id=contract.id,
            compensation_date=date(2026,9,30), acquisition_bucket=bucket)
        with pytest.raises(PayrollAverageHistoryError, match='documented employment fraction'):
            await derive_compensation_average(db, **args)
        basis=await derive_compensation_average(db, **args, salary_substitution=CompensationSalarySubstitution(
            employment_fraction=D('.5'), source_reference='Half-time employment agreement'))
        assert basis['eligible_earnings'] == D('4323.5')*12
        assert basis['eligible_days'] == 365
        assert basis['sources'][0]['salary_amount'] == 3000
        assert basis['reference_period_start'] == (date(2023,1,1) if bucket=='before_2024' else date(2025,9,1))
        if not short_service:
            from sqlalchemy import delete
            await db.execute(delete(PayrollEarningsHistory).where(PayrollEarningsHistory.company_id==company.id,
                PayrollEarningsHistory.month==basis['reference_period_start']))
            with pytest.raises(PayrollAverageHistoryError, match='Missing or ambiguous'):
                await derive_compensation_average(db, **args, salary_substitution=CompensationSalarySubstitution(
                    employment_fraction=D('.5'), source_reference='Half-time employment agreement'))
