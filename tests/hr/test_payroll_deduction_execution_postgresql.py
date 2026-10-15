"""Operational deductions: partial payouts, real postings and bank remittances."""
import ast
import os
from pathlib import Path
from datetime import date, datetime, timezone
from decimal import Decimal as D
import pytest
from sqlalchemy import select, text, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import DBAPIError
from test_employee_foundation import employee_engine, minimal_kwargs
from test_payroll_insurance_funding import setup as payroll_setup
from app.models.user import User
from app.models.employee import Employee
from app.models.account import Account
from app.models.permission import Permission
from app.models.role import Role
from app.models.rbac import role_permissions
from app.models.user_company_role import UserCompanyRole
from app.models.payroll_deduction import PayrollDeductionInstruction
from app.models.payroll_deduction_legal_evidence import PayrollDeductionLegalEvidence
from app.models.payroll_deduction_rule_approval import PayrollDeductionRuleApproval
from app.models.payroll_deduction_execution import PayrollWithholdingBatch as Batch, PayrollWithholdingLine as Line, PayrollDeductionAssessment as Assessment
from app.models.payroll_disbursement import PayrollDisbursement
from app.models.journal_entry import JournalEntry
from app.models.journal_entry_line import JournalEntryLine
from app.models.bank_statement import BankStatement
from app.models.bank_statement_line import BankStatementLine
from app.schemas.payroll_deduction_beneficiary import DeductionBeneficiaryCreate
from app.schemas.payroll_deduction_execution import AssessmentCreate, WithholdingPaymentCreate, RecipientRemittanceCreate
from app.services.payroll_deduction_beneficiary_service import create_beneficiary, verify_beneficiary
from app.services.payroll_deduction_execution_service import create_assessment, execute_payment, recipient_balances, DeductionExecutionError
from app.services.payroll_recipient_remittance_service import register_recipient_remittance, reverse_recipient_remittance
from app.services.accounting_posting import post_journal_entry
from app.services.accounting_reversal import reverse_journal_entry, AccountingReversalError
from app.services.payroll_employee_balance_service import employee_payroll_balance

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(os.getenv('RUN_POSTGRES_E2E') != '1', reason='Isolated PostgreSQL')]
IBAN = 'UA903052992990004149123456789'


async def protect(engine):
    async with engine.begin() as conn:
        for revision in ('029','030'):
            source = next(Path('alembic/versions').glob(f'13d4a7b8c{revision}_*.py')).read_text()
            for node in ast.walk(ast.parse(source)):
                if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute) and node.func.attr=='execute' and node.args and isinstance(node.args[0],ast.Constant):
                    sql = node.args[0].value
                    if sql.lstrip().startswith(('CREATE FUNCTION','CREATE TRIGGER','CREATE CONSTRAINT TRIGGER')):
                        await conn.execute(text(sql))


async def authorize(db, company, user, contract, claim_amount=D(1000)):
    verifier = User(**minimal_kwargs(User, dict(email=f'verifier-{company.id}@example.test',password_hash='unused',first_name='Verifier',last_name='Test')))
    role = Role(name=f'deduction-manager-{company.id}')
    permission = Permission(name='employees.manage')
    account = Account(company_id=company.id,code='6851',name='Recipients',account_type='liability',normal_balance='credit',is_active=True,is_postable=True)
    db.add_all([verifier,role,permission,account]);await db.flush()
    await db.execute(role_permissions.insert(),dict(role_id=role.id,permission_id=permission.id))
    db.add_all([UserCompanyRole(company_id=company.id,user_id=uid,role_id=role.id,is_active=True) for uid in (user.id,verifier.id)])
    instruction = PayrollDeductionInstruction(company_id=company.id,employment_contract_id=contract.id,
        deduction_type='enforcement',method='fixed',fixed_amount=D(1000),priority=1,currency_code='UAH',
        effective_from=date(2026,9,1),request_key='claim',created_by=user.id,created_at=datetime.now(timezone.utc))
    db.add(instruction);await db.flush()
    db.add(PayrollDeductionLegalEvidence(company_id=company.id,payroll_deduction_instruction_id=instruction.id,
        legal_category='enforcement_other',document_reference='Verified order',verification_status='verified',
        verified_by=verifier.id,verified_at=datetime.now(timezone.utc),created_by=user.id,created_at=datetime.now(timezone.utc)))
    approval = PayrollDeductionRuleApproval(company_id=company.id,legal_category='enforcement_other',rule_version='2026-manual-assessment-v1',
        legal_reference='Article 70: independently approved rule',valid_from=date(2026,1,1),status='approved',
        created_by=user.id,created_at=datetime.now(timezone.utc),approved_by=verifier.id,approved_at=datetime.now(timezone.utc))
    db.add(approval);await db.flush()
    beneficiary = await create_beneficiary(db,company_id=company.id,instruction_id=instruction.id,actor_id=user.id,
        data=DeductionBeneficiaryCreate(request_key='recipient',recipient_name='Executor',recipient_identifier='TEST-ID',iban=IBAN,
            settlement_kind='recipient_payment',account_id=account.id,document_reference='Verified bank details',valid_from=date(2026,9,1)))
    await verify_beneficiary(db,company_id=company.id,beneficiary_id=beneficiary.id,actor_id=verifier.id)
    claim = await create_assessment(db,company_id=company.id,actor_id=user.id,data=AssessmentCreate(
        instruction_id=instruction.id,beneficiary_id=beneficiary.id,rule_approval_id=approval.id,reference='New September claim; no duplicated carry',
        request_key='assessment',amount=claim_amount,priority=1,priority_reference='Verified enforcement order',
        individual_limit_percentage=D(20),effective_from=date(2026,9,1)))
    return beneficiary,claim


async def seed(db, claim_amount=D(1000)):
    company,user,contract,calc,accrual,accounts,bank=await payroll_setup(db)
    employee=await db.get(Employee,contract.employee_id);employee.payment_iban=IBAN;await db.flush()
    beneficiary,claim=await authorize(db,company,user,contract,claim_amount)
    return company,user,contract,calc,bank,beneficiary,claim


def request(calc,bank,key,amount=D(1000),protected=D(0)):
    return WithholdingPaymentCreate(kind='salary',payroll_calculation_id=calc.id,bank_account_id=bank.id,
        payment_date=date(2026,9,30),amount=amount,protected_amount=protected,
        income_reference='Accountant-confirmed composition of net income',request_key=key)


async def test_partial_payment_accounting_and_idempotency(employee_engine):
    await protect(employee_engine)
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,user,contract,calc,bank,beneficiary,claim=await seed(db)
        data=request(calc,bank,'part-1',protected=D(100))
        batch=await execute_payment(db,company_id=company.id,data=data,actor_id=user.id)
        assert batch.withheld_amount == D(180)
        payout=await db.get(PayrollDisbursement,batch.disbursement_id)
        assert payout.amount == D(820) and payout.withheld_amount == D(180)
        assert (await execute_payment(db,company_id=company.id,data=data,actor_id=user.id)).id == batch.id
        with pytest.raises(DeductionExecutionError,match='different data'):
            await execute_payment(db,company_id=company.id,data=data.model_copy(update={'protected_amount':D(0)}),actor_id=user.id)
        second=await execute_payment(db,company_id=company.id,data=request(calc,bank,'part-2'),actor_id=user.id)
        assert second.withheld_amount == D(200)
        balances=await recipient_balances(db,company_id=company.id,as_of=date(2026,9,30))
        assert balances['recipients'][0]['balance']==D(380)
        employee_balance=await employee_payroll_balance(db,company_id=company.id,employee_id=contract.employee_id,as_of=date(2026,9,30))
        assert employee_balance['balances'][0]['deducted_amount']==D(380)
        assert employee_balance['balances'][0]['paid_net']==D(1620)
        await db.commit()
        with pytest.raises(DBAPIError):
            async with db.begin_nested():
                await db.execute(text('UPDATE payroll_withholding_batches SET withheld_amount=0 WHERE id=:id'),dict(id=batch.id))
        with pytest.raises(DBAPIError):
            async with db.begin_nested():
                await db.execute(text('UPDATE payroll_disbursements SET amount=999 WHERE id=:id'),dict(id=payout.id))


async def test_actual_bank_remittance_and_reversal_order(employee_engine):
    await protect(employee_engine)
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,user,contract,calc,bank,beneficiary,claim=await seed(db)
        batch=await execute_payment(db,company_id=company.id,data=request(calc,bank,'salary'),actor_id=user.id)
        line=await db.scalar(select(Line).where(Line.batch_id==batch.id))
        payment=JournalEntry(company_id=company.id,entry_date=date(2026,9,30),description='Actual recipient bank payment',status='draft',created_by=user.id)
        payment.lines=[JournalEntryLine(line_no=1,account_id=beneficiary.account_id,debit=D(100),credit=D(0)),
            JournalEntryLine(line_no=2,account_id=bank.accounting_account_id,debit=D(0),credit=D(100))]
        db.add(payment);await db.flush();await post_journal_entry(db,company.id,payment.id)
        statement=BankStatement(company_id=company.id,bank_account_id=bank.id,external_id='recipient-statement')
        db.add(statement);await db.flush()
        evidence=BankStatementLine(company_id=company.id,bank_statement_id=statement.id,bank_account_id=bank.id,
            external_line_id='recipient-transfer',transaction_date=date(2026,9,30),amount=D(-100),currency_code='UAH',counterparty_account=IBAN)
        db.add(evidence);await db.flush()
        data=RecipientRemittanceCreate(beneficiary_id=beneficiary.id,journal_entry_id=payment.id,bank_statement_line_id=evidence.id,
            request_key='remit',allocations=[dict(withholding_line_id=line.id,amount=D(100))])
        with pytest.raises(DeductionExecutionError,match='outgoing amount'):
            await register_recipient_remittance(db,company_id=company.id,actor_id=user.id,
                data=RecipientRemittanceCreate(**{**data.model_dump(),'allocations':[dict(withholding_line_id=line.id,amount=D(99))]}))
        with pytest.raises(DeductionExecutionError,match='Duplicate withholding'):
            await register_recipient_remittance(db,company_id=company.id,actor_id=user.id,
                data=RecipientRemittanceCreate(**{**data.model_dump(),'allocations':[
                    dict(withholding_line_id=line.id,amount=D(50)),dict(withholding_line_id=line.id,amount=D(50))]}))
        evidence.counterparty_account='UA213223130000026007233566001';await db.flush()
        with pytest.raises(DeductionExecutionError,match='recipient IBAN'):
            await register_recipient_remittance(db,company_id=company.id,data=data,actor_id=user.id)
        evidence.counterparty_account=IBAN;await db.flush()
        before=await db.scalar(select(func.count(JournalEntry.id)))
        remit=await register_recipient_remittance(db,company_id=company.id,data=data,actor_id=user.id)
        assert await db.scalar(select(func.count(JournalEntry.id)))==before
        assert (await register_recipient_remittance(db,company_id=company.id,data=data,actor_id=user.id)).id==remit.id
        with pytest.raises(DeductionExecutionError,match='already attributed'):
            await register_recipient_remittance(db,company_id=company.id,actor_id=user.id,
                data=data.model_copy(update={'request_key':'duplicate-bank'}))
        journal=await db.scalar(select(JournalEntry).where(JournalEntry.payroll_disbursement_id==batch.disbursement_id))
        with pytest.raises(AccountingReversalError,match='recipient remittances'):
            await reverse_journal_entry(db,company.id,journal.id,date(2026,9,30),user.id)
        assert (await recipient_balances(db,company_id=company.id,as_of=date(2026,9,30)))['recipients'][0]['balance']==100
        await reverse_recipient_remittance(db,company_id=company.id,remittance_id=remit.id,reversal_date=date(2026,9,30),actor_id=user.id)
        await reverse_journal_entry(db,company.id,journal.id,date(2026,9,30),user.id)
        assert (await recipient_balances(db,company_id=company.id,as_of=date(2026,9,30)))['recipients']==[]


async def test_automatic_advance_withholds_only_confirmed_claim(employee_engine):
    from test_payroll_source_locking import sources
    from app.models.company import Company
    from app.models.payroll import PayrollPeriod
    from app.models.payroll_advance import PayrollAdvance
    from app.models.bank_account import BankAccount
    from app.models.accounting_period import AccountingPeriod
    from app.models.payroll_statutory import PayrollStatutoryComponent as Component
    from app.schemas.time_attendance import AttendanceCreate
    from app.services.time_attendance_service import create_attendance
    from app.services.payroll_statutory_service import create_statutory_rate
    from zoneinfo import ZoneInfo
    await protect(employee_engine)
    async with AsyncSession(employee_engine, expire_on_commit=False) as db:
        f, contract, _ = await sources(db)
        company, user = await db.get(Company, f['company']), await db.get(User, f['actor'])
        period = PayrollPeriod(company_id=company.id, year=2026, month=9,
            start_date=date(2026,9,1), end_date=date(2026,9,30), status='draft', created_by=user.id)
        bank_gl = Account(company_id=company.id, code='311', name='Bank', account_type='asset',
            normal_balance='debit', is_active=True, is_postable=True)
        payable = Account(company_id=company.id, code='66', name='Payroll payable', account_type='liability',
            normal_balance='credit', is_active=True, is_postable=True, is_system=True)
        db.add_all([period, bank_gl, payable, AccountingPeriod(company_id=company.id,year=2026,month=9,
            start_date=date(2026,9,1),end_date=date(2026,9,30),status='open',is_locked=False)])
        await db.flush()
        bank = BankAccount(company_id=company.id,name='Payroll bank',account_number='advance-test',
            currency_code='UAH',accounting_account_id=bank_gl.id,is_active=True)
        db.add(bank); await db.flush()
        for day in range(1,16):
            if date(2026,9,day).weekday()>4: continue
            await create_attendance(db,company_id=company.id,created_by=user.id,
                data=AttendanceCreate(employment_contract_id=contract.id,work_date=date(2026,9,day),status='present',
                    actual_start_at=datetime(2026,9,day,9,tzinfo=ZoneInfo('Europe/Kyiv')),
                    actual_end_at=datetime(2026,9,day,17,tzinfo=ZoneInfo('Europe/Kyiv'))))
        for component, rate in ((Component.PERSONAL_INCOME_TAX,'.18'),(Component.MILITARY_LEVY,'.05'),(Component.UNIFIED_SOCIAL_CONTRIBUTION,'.22')):
            await create_statutory_rate(db,company_id=company.id,component=component,rate=D(rate),
                effective_from=date(2026,1,1),effective_to=None,actor_user_id=user.id)
        beneficiary, claim = await authorize(db,company,user,contract)
        data = WithholdingPaymentCreate(kind='advance',payroll_period_id=period.id,employment_contract_id=contract.id,
            bank_account_id=bank.id,payment_date=date(2026,9,20),advance_percentage=D(40),protected_amount=D(0),
            income_reference='Confirmed ordinary salary income',request_key='advance-withholding')
        batch = await execute_payment(db,company_id=company.id,data=data,actor_id=user.id)
        advance = await db.get(PayrollAdvance,batch.advance_id)
        assert batch.net_before == D(8470)
        assert batch.withheld_amount == claim.amount == D(1000)
        assert advance.paid_amount == D(7470)
        assert advance.paid_amount + advance.withheld_amount == advance.minimum_due_amount
        assert (await execute_payment(db,company_id=company.id,data=data,actor_id=user.id)).id == batch.id
        journal = await db.scalar(select(JournalEntry).where(JournalEntry.payroll_advance_id==advance.id))
        entries = (await db.execute(select(JournalEntryLine.account_id,JournalEntryLine.debit,JournalEntryLine.credit)
            .where(JournalEntryLine.journal_entry_id==journal.id))).all()
        assert sorted(entries) == sorted([(payable.id,D(8470),D(0)),(bank_gl.id,D(0),D(7470)),(beneficiary.account_id,D(0),D(1000))])
        assert (await recipient_balances(db,company_id=company.id,as_of=date(2026,9,20)))['recipients'][0]['balance'] == D(1000)

        # Close the same month: the advance offsets both cash and withheld debt.
        from app.schemas.payroll import PayrollInputCreate
        from app.services.payroll_input_service import create_payroll_input
        from app.services.payroll_period_service import finalize_payroll_period
        from app.services.payroll_calculation_service import calculate_payroll_input
        from app.services.payroll_statutory_service import calculate_payroll_statutory_result
        from app.services.payroll_accounting_service import generate_and_post_payroll_journal_entry
        for day in range(16,31):
            if date(2026,9,day).weekday()>4: continue
            await create_attendance(db,company_id=company.id,created_by=user.id,
                data=AttendanceCreate(employment_contract_id=contract.id,work_date=date(2026,9,day),status='present',
                    actual_start_at=datetime(2026,9,day,9,tzinfo=ZoneInfo('Europe/Kyiv')),
                    actual_end_at=datetime(2026,9,day,17,tzinfo=ZoneInfo('Europe/Kyiv'))))
        for code,kind,normal in [('92','expense','debit'),('64','liability','credit'),('65','liability','credit')]:
            db.add(Account(company_id=company.id,code=code,name=code,account_type=kind,
                normal_balance=normal,is_active=True,is_postable=True,is_system=True))
        employee=await db.get(Employee,contract.employee_id);employee.payment_iban=IBAN
        await db.flush()
        inputs=await create_payroll_input(db,company_id=company.id,payroll_period_id=period.id,
            created_by=user.id,data=PayrollInputCreate(employment_contract_id=contract.id))
        await finalize_payroll_period(db,company_id=company.id,payroll_period_id=period.id,finalized_by=user.id)
        calc=await calculate_payroll_input(db,company_id=company.id,payroll_input_id=inputs.id,calculated_by=user.id)
        tax=await calculate_payroll_statutory_result(db,company_id=company.id,payroll_calculation_id=calc.id,actor_user_id=user.id)
        assert tax.net_amount==D(16940)
        await generate_and_post_payroll_journal_entry(db,company_id=company.id,payroll_calculation_id=calc.id,created_by=user.id)
        final=await execute_payment(db,company_id=company.id,
            data=request(calc,bank,'month-end',amount=None),actor_id=user.id)
        assert final.net_before==D(8470) and final.withheld_amount==0
        balance=await employee_payroll_balance(db,company_id=company.id,employee_id=contract.employee_id,as_of=date(2026,9,30))
        assert balance['balances'][0]['balance']==0
        assert balance['balances'][0]['advance_paid']==D(7470)
        assert balance['balances'][0]['paid_net']==D(8470)
        assert balance['balances'][0]['deducted_amount']==D(1000)


async def test_later_payment_reversal_does_not_release_past_salary_capacity(employee_engine):
    from app.models.accounting_period import AccountingPeriod
    from app.models.payroll_statutory import PayrollStatutoryResult
    from app.services.payroll_disbursement_service import PayrollDisbursementSourceStateError
    await protect(employee_engine)
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,user,contract,calc,bank,beneficiary,claim = await seed(db)
        db.add(AccountingPeriod(company_id=company.id,year=2026,month=10,start_date=date(2026,10,1),
            end_date=date(2026,10,31),status='open',is_locked=False))
        await db.flush()
        batch = await execute_payment(db,company_id=company.id,data=request(calc,bank,'original'),actor_id=user.id)
        journal = await db.scalar(select(JournalEntry).where(JournalEntry.payroll_disbursement_id==batch.disbursement_id))
        await reverse_journal_entry(db,company.id,journal.id,date(2026,10,5),user.id)
        statutory = await db.scalar(select(PayrollStatutoryResult).where(PayrollStatutoryResult.payroll_calculation_id==calc.id))
        with pytest.raises(PayrollDisbursementSourceStateError,match='exceeds remaining'):
            await execute_payment(db,company_id=company.id,
                data=request(calc,bank,'backdated',amount=statutory.net_amount),actor_id=user.id)
        assert await db.scalar(select(func.count(Batch.id))) == 1


async def test_withholding_http_details_permissions_and_company_isolation(employee_engine):
    from fastapi import FastAPI
    from httpx import AsyncClient, ASGITransport
    from app.api.v1.payroll_deductions import router
    from app.api.deps import get_current_user
    from app.core.database import get_db
    from types import SimpleNamespace
    await protect(employee_engine)
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,user,contract,calc,bank,beneficiary,claim = await seed(db)
        permission = Permission(name='employees.read'); db.add(permission); await db.flush()
        role_id = await db.scalar(select(UserCompanyRole.role_id).where(UserCompanyRole.user_id==user.id))
        await db.execute(role_permissions.insert(),dict(role_id=role_id,permission_id=permission.id))
        await db.commit()
        api=FastAPI(); api.include_router(router)
        async def session(): yield db
        api.dependency_overrides[get_db]=session
        api.dependency_overrides[get_current_user]=lambda:SimpleNamespace(id=user.id)
        async with AsyncClient(transport=ASGITransport(app=api),base_url='http://test') as client:
            created=await client.post(f'/companies/{company.id}/payroll-withholding-payments',json=request(calc,bank,'api').model_dump(mode='json'))
            assert created.status_code==201,created.text
            bid=created.json()['id']
            details=await client.get(f'/companies/{company.id}/payroll-withholding-payments/{bid}')
            assert details.status_code==200,details.text
            assert len(details.json()['lines'])==1
            assert details.json()['lines'][0]['assessment_id']==claim.id
            assert 'creation_xid' not in details.json()
            missing=await client.get(f'/companies/{company.id}/payroll-withholding-payments/{bid+10000}')
            assert missing.status_code==404
            foreign=await client.get(f'/companies/{company.id+10000}/payroll-withholding-payments/{bid}')
            assert foreign.status_code in (403,404)
            claims=await client.get(f'/companies/{company.id}/payroll-deductions/{claim.instruction_id}/assessments')
            assert claims.status_code==200 and claims.json()[0]['id']==claim.id


async def test_register_and_payslip_show_actual_partial_withholding(employee_engine):
    from app.services.payroll_register_service import get_payroll_register
    from app.services.payroll_payslip_service import generate_payroll_payslip, list_payroll_payslip_lines
    await protect(employee_engine)
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,user,contract,calc,bank,beneficiary,claim = await seed(db)
        batch = await execute_payment(db,company_id=company.id,data=request(calc,bank,'report'),actor_id=user.id)
        register = await get_payroll_register(db,company_id=company.id,payroll_period_id=calc.payroll_period_id)
        row = next(row for row in register.rows if row.payroll_calculation_id==calc.id)
        assert row.deduction_amount == row.deduction_posted_amount == D(200)
        assert row.paid_amount == D(800)
        assert row.recognized_balance == row.net_amount - D(1000)
        slip = await generate_payroll_payslip(db,company_id=company.id,payroll_calculation_id=calc.id,actor_user_id=user.id)
        lines = await list_payroll_payslip_lines(db,company_id=company.id,payroll_payslip_id=slip.id)
        operational = [line for line in lines if line.source_withholding_line_id is not None]
        assert len(operational)==1 and operational[0].amount==D(200)
        source = await db.get(Line,operational[0].source_withholding_line_id)
        assert source.batch_id==batch.id


async def test_concurrent_payments_cannot_spend_confirmed_claim_twice(employee_engine):
    import asyncio
    await protect(employee_engine)
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,user,contract,calc,bank,beneficiary,claim = await seed(db,claim_amount=D(100))
        company_id,actor_id,claim_id=company.id,user.id,claim.id
        requests=[request(calc,bank,f'concurrent-{i}') for i in range(2)]
        await db.commit()
    async def pay(data):
        async with AsyncSession(employee_engine,expire_on_commit=False) as db:
            batch=await execute_payment(db,company_id=company_id,data=data,actor_id=actor_id)
            await db.commit()
            return batch.withheld_amount
    amounts=await asyncio.gather(*(pay(data) for data in requests))
    assert sorted(amounts)==[D(0),D(100)]
    async with AsyncSession(employee_engine) as db:
        assert await db.scalar(select(func.sum(Line.amount)).where(Line.assessment_id==claim_id))==D(100)


async def test_document_and_rule_http_independent_approval(employee_engine):
    from fastapi import FastAPI
    from httpx import AsyncClient, ASGITransport
    from app.api.v1.payroll_deductions import router
    from app.api.deps import get_current_user
    from app.core.database import get_db
    from types import SimpleNamespace
    await protect(employee_engine)
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,user,contract,calc,bank,beneficiary,claim = await seed(db)
        verifier=await db.scalar(select(User).where(User.email==f'verifier-{company.id}@example.test'))
        role_id=await db.scalar(select(UserCompanyRole.role_id).where(UserCompanyRole.user_id==user.id))
        for action in ('create','approve','revoke'):
            permission=Permission(name=f'payroll_deduction_rule_approval.{action}');db.add(permission);await db.flush()
            await db.execute(role_permissions.insert(),dict(role_id=role_id,permission_id=permission.id))
        instruction=PayrollDeductionInstruction(company_id=company.id,employment_contract_id=contract.id,
            deduction_type='enforcement',method='fixed',fixed_amount=D(100),priority=1,currency_code='UAH',
            effective_from=date(2026,10,1),request_key='http-evidence',created_by=user.id,created_at=datetime.now(timezone.utc))
        db.add(instruction);await db.commit()
        actor=SimpleNamespace(id=user.id)
        api=FastAPI();api.include_router(router)
        async def session():yield db
        api.dependency_overrides[get_db]=session
        api.dependency_overrides[get_current_user]=lambda:actor
        company_id,instruction_id,user_id,verifier_id=company.id,instruction.id,user.id,verifier.id
        async with AsyncClient(transport=ASGITransport(app=api),base_url='http://test') as client:
            evidence=await client.post(f'/companies/{company_id}/payroll-deductions/{instruction_id}/legal-evidence',
                json=dict(legal_category='enforcement_other',document_reference='New verified source'))
            assert evidence.status_code==201,evidence.text
            evidence_id=evidence.json()['id']
            refused=await client.post(f'/companies/{company_id}/payroll-deduction-evidence/{evidence_id}/verify')
            assert refused.status_code==409
            actor.id=verifier_id
            verified=await client.post(f'/companies/{company_id}/payroll-deduction-evidence/{evidence_id}/verify')
            assert verified.status_code==200 and verified.json()['verification_status']=='verified'
            actor.id=user_id
            created=await client.post(f'/companies/{company_id}/payroll-deduction-rule-approvals',json=dict(
                legal_category='enforcement_other',rule_version='http-2026',legal_reference='Reviewed rule',valid_from='2026-10-01'))
            assert created.status_code==201,created.text
            aid=created.json()['id']
            refused=await client.post(f'/companies/{company_id}/payroll-deduction-rule-approvals/{aid}/approve')
            assert refused.status_code==409
            actor.id=verifier_id
            approved=await client.post(f'/companies/{company_id}/payroll-deduction-rule-approvals/{aid}/approve')
            assert approved.status_code==200 and approved.json()['status']=='approved'
            revoked=await client.post(f'/companies/{company_id}/payroll-deduction-rule-approvals/{aid}/revoke')
            assert revoked.status_code==200 and revoked.json()['status']=='revoked'


async def test_opening_debt_partial_withholding_settles_without_new_income(employee_engine):
    from test_payroll_opening_disbursement import seed_opening
    from test_employment_structure import contract as create_contract
    from app.models.company import Company
    from app.models.accounting_period import AccountingPeriod
    from app.services.payroll_disbursement_service import PayrollDisbursementSourceStateError
    await protect(employee_engine)
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        f,bank,debt=await seed_opening(db)
        company,user=await db.get(Company,f['company']),await db.get(User,f['actor'])
        contract=await create_contract(db,f)
        db.add(AccountingPeriod(company_id=company.id,year=2026,month=9,start_date=date(2026,9,1),
            end_date=date(2026,9,30),status='open',is_locked=False));await db.flush()
        beneficiary,claim=await authorize(db,company,user,contract)
        data=WithholdingPaymentCreate(kind='opening',opening_debt_id=debt.id,bank_account_id=bank.id,
            payment_date=date(2026,9,30),amount=D(400),protected_amount=D(0),income_reference='Confirmed opening salary income',request_key='opening-first')
        first=await execute_payment(db,company_id=company.id,data=data,actor_id=user.id)
        assert first.withheld_amount==D(80)
        with pytest.raises(PayrollDisbursementSourceStateError,match='exceeds'):
            await execute_payment(db,company_id=company.id,data=data.model_copy(update={'request_key':'excess','amount':D(601)}),actor_id=user.id)
        final=await execute_payment(db,company_id=company.id,data=data.model_copy(update={'request_key':'opening-final','amount':None}),actor_id=user.id)
        assert final.net_before==D(600) and final.withheld_amount==D(120)
        balance=await employee_payroll_balance(db,company_id=company.id,employee_id=contract.employee_id,as_of=date(2026,9,30))
        assert balance['balances'][0]['balance']==0
        assert balance['balances'][0]['accrued_net']==0
        assert balance['balances'][0]['deducted_amount']==200
        assert balance['balances'][0]['paid_net']==800
