from datetime import date
from decimal import Decimal as D
import pytest
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from test_employee_foundation import employee_engine
from test_payroll_secondary_sick_postgresql import prepare
from test_payroll_sick_correction_preview_postgresql import changed_evidence, install_revision_guards
from test_payroll_sick_payroll_revision_postgresql import payroll_for_sick
from test_payroll_correction_basis_postgresql import protect
from app.models.account import Account
from app.models.accounting_period import AccountingPeriod
from app.models.bank_account import BankAccount
from app.models.employee import Employee
from app.models.journal_entry import JournalEntry
from app.models.payroll_statutory import PayrollStatutoryComponent as Component
from app.models.payroll_sick_correction_application import PayrollSickPayrollRevision
from app.services.payroll_sick_history_service import calculate_sick_from_history
from app.services.payroll_statutory_service import create_statutory_rate, calculate_payroll_statutory_result
from app.services.payroll_accounting_service import generate_and_post_payroll_journal_entry
from app.services.payroll_sick_correction_application_service import apply_sick_correction
from app.services.payroll_disbursement_service import create_payroll_disbursement, generate_and_post_payroll_disbursement_journal, PayrollDisbursementSourceStateError

pytestmark = pytest.mark.asyncio


async def setup(db, *, service_months=120, post=True):
    from test_payroll_insurance_evidence import insurance_setup
    from app.services.payroll_insurance_evidence_service import review_insurance_evidence
    company, user, contract, leave, reviewer, _, evidence = await insurance_setup(db, total=service_months)
    await review_insurance_evidence(db, company_id=company.id, evidence_id=evidence.id,
        reviewed_by=reviewer.id, approved=True)
    args = dict(company_id=company.id, leave_request_id=leave.id,
        insurance_evidence_id=evidence.id, calculated_by=user.id)
    sick = await calculate_sick_from_history(db, **args)
    calc = await payroll_for_sick(db, company, sick, args['calculated_by'])
    for component, rate in [(Component.PERSONAL_INCOME_TAX, D('.18')),
            (Component.MILITARY_LEVY, D('.05')), (Component.UNIFIED_SOCIAL_CONTRIBUTION, D('.22'))]:
        await create_statutory_rate(db, company_id=company.id, component=component, rate=rate,
            effective_from=date(2026,1,1), effective_to=None, actor_user_id=args['calculated_by'])
    tax = await calculate_payroll_statutory_result(db, company_id=company.id,
        payroll_calculation_id=calc.id, actor_user_id=args['calculated_by'])
    for month in (9,10):
        db.add(AccountingPeriod(company_id=company.id, year=2026, month=month,
            start_date=date(2026,month,1), end_date=date(2026,month,30 if month==9 else 31),
            status='open', is_locked=False))
    accounts = {}
    for code in ('92','94','378','66','64','65','311'):
        kind = 'expense' if code in ('92','94') else 'asset' if code in ('378','311') else 'liability'
        row = Account(company_id=company.id, code=code, name='Payroll '+code, account_type=kind,
            normal_balance='credit' if kind=='liability' else 'debit', is_postable=True,
            is_system=True, is_active=True)
        db.add(row)
        await db.flush()
        accounts[code] = row.id
    bank = BankAccount(company_id=company.id, name='Payroll bank', account_number='test-bank',
        currency_code='UAH', accounting_account_id=accounts['311'], is_active=True)
    db.add(bank)
    employee = await db.get(Employee, contract.employee_id)
    employee.payment_iban = 'UA213223130000026007233566001'
    await db.flush()
    journal = (await generate_and_post_payroll_journal_entry(db, company_id=company.id,
        payroll_calculation_id=calc.id, created_by=args['calculated_by'])) if post else None
    return company, args, sick, calc, tax, bank, journal


async def test_paid_sick_decrease_keeps_original_payment_and_prevents_second_payout(employee_engine):
    await install_revision_guards(employee_engine)
    await protect(employee_engine)
    async with AsyncSession(employee_engine, expire_on_commit=False) as db:
        company, args, sick, calc, tax, bank, original_journal = await setup(db)
        payout = await create_payroll_disbursement(db, company_id=company.id,
            payroll_calculation_id=calc.id, bank_account_id=bank.id, payment_date=date(2026,9,30),
            created_by=args['calculated_by'], request_key='original-paid')
        payment = await generate_and_post_payroll_disbursement_journal(db, company_id=company.id,
            payroll_disbursement_id=payout.id, created_by=args['calculated_by'])
        evidence = await changed_evidence(db, company, args)
        request = dict(company_id=company.id, original_sick_id=sick.id,
            insurance_evidence_id=evidence.id, correction_date=date(2026,10,26),
            request_key='apply-sick', reason='Verified correction of insurance history', actor_id=args['calculated_by'])
        application = await apply_sick_correction(db, **request)
        edge = await db.scalar(select(PayrollSickPayrollRevision).where(
            PayrollSickPayrollRevision.application_id==application.id))
        assert edge.net_delta < 0
        assert original_journal.status == 'posted' and payment.status == 'posted'
        assert payout.cancelled_at is None and payout.reversed_on is None
        journal_count = await db.scalar(select(func.count()).select_from(JournalEntry))
        assert (await apply_sick_correction(db, **request)).id == application.id
        assert await db.scalar(select(func.count()).select_from(JournalEntry)) == journal_count
        with pytest.raises(PayrollDisbursementSourceStateError, match='No positive remaining'):
            await create_payroll_disbursement(db, company_id=company.id,
                payroll_calculation_id=edge.replacement_calculation_id, bank_account_id=bank.id,
                payment_date=date(2026,10,26), created_by=args['calculated_by'], request_key='duplicate')

        from app.services.payroll_sick_settlement_service import sick_settlement_summary
        from app.services.payroll_sick_return_service import register_sick_return, SickReturnError
        from app.models.journal_entry_line import JournalEntryLine
        from app.services.accounting_posting import post_journal_entry
        from app.services.payroll_employee_balance_service import employee_payroll_balance
        from app.models.employment_contract import EmploymentContract
        summary = await sick_settlement_summary(db, company_id=company.id,
            calculation_id=edge.replacement_calculation_id, as_of=date(2026,10,26))
        assert summary['employee_overpayment'] == -edge.net_delta
        payable = await db.scalar(select(Account.id).where(Account.company_id==company.id, Account.code=='66'))
        returned = JournalEntry(company_id=company.id, entry_date=date(2026,10,26),
            status='draft', created_by=args['calculated_by'], description='Confirmed employee repayment')
        returned.lines = [JournalEntryLine(line_no=1, account_id=bank.accounting_account_id,
            debit=-edge.net_delta, credit=0), JournalEntryLine(line_no=2, account_id=payable,
            debit=0, credit=-edge.net_delta)]
        db.add(returned)
        await db.flush()
        await post_journal_entry(db, company.id, returned.id)
        return_args = dict(company_id=company.id, calculation_id=edge.replacement_calculation_id,
            journal_entry_id=returned.id, bank_account_id=bank.id, kind='employee',
            request_key='confirmed-return', document_reference='Employee confirmed bank return document',
            actor_id=args['calculated_by'])
        receipt = await register_sick_return(db, **return_args)
        assert (await register_sick_return(db, **return_args)).id == receipt.id
        with pytest.raises(SickReturnError, match='already attributed'):
            await register_sick_return(db, **dict(return_args, request_key='duplicate-return'))
        summary = await sick_settlement_summary(db, company_id=company.id,
            calculation_id=edge.replacement_calculation_id, as_of=date(2026,10,26))
        assert summary['employee_overpayment'] == summary['employee_due'] == 0
        contract = await db.get(EmploymentContract, calc.employment_contract_id)
        balance = await employee_payroll_balance(db, company_id=company.id,
            employee_id=contract.employee_id, as_of=date(2026,10,26))
        assert balance['complete'] and balance['balances'][0]['balance']==0

        # A further reviewed correction must retain both the first payment and
        # its actual return, without consuming either twice.
        next_evidence = await changed_evidence(db,company,dict(args,insurance_evidence_id=evidence.id))
        next_application = await apply_sick_correction(db,company_id=company.id,
            original_sick_id=application.replacement_sick_id,insurance_evidence_id=next_evidence.id,
            correction_date=date(2026,10,26),request_key='second-correction',
            reason='Further independently reviewed insurance correction',actor_id=args['calculated_by'])
        next_id = next_application.payroll_calculation_ids[0]
        next_summary = await sick_settlement_summary(db,company_id=company.id,
            calculation_id=next_id,as_of=date(2026,10,26))
        assert next_summary['employee_due']==receipt.amount
        assert next_summary['employee_overpayment']==0
        next_payout = await create_payroll_disbursement(db,company_id=company.id,
            payroll_calculation_id=next_id,bank_account_id=bank.id,payment_date=date(2026,10,26),
            created_by=args['calculated_by'],request_key='second-correction-difference')
        from app.api.v1.payroll_sick_correction_applications import application_response
        response = await application_response(db,application,as_of=date(2026,10,26))
        assert response['settlements'][0]['payroll_calculation_id']==next_id
        assert next_payout.amount==receipt.amount
        await generate_and_post_payroll_disbursement_journal(db,company_id=company.id,
            payroll_disbursement_id=next_payout.id,created_by=args['calculated_by'])
        from sqlalchemy import text
        await db.execute(text('SET CONSTRAINTS ALL IMMEDIATE'))


@pytest.mark.parametrize('paid_fraction', [D('0'), D('.5'), D('1')])
async def test_sick_increase_pays_only_remaining_difference(employee_engine, paid_fraction):
    from app.models.payroll_statutory import PayrollStatutoryResult
    from app.services.payroll_employee_balance_service import employee_payroll_balance
    from app.models.employment_contract import EmploymentContract
    await install_revision_guards(employee_engine)
    await protect(employee_engine)
    async with AsyncSession(employee_engine, expire_on_commit=False) as db:
        company, args, sick, calc, tax, bank, original_journal = await setup(db, service_months=12)
        already_paid = (tax.net_amount * paid_fraction).quantize(D('.01'))
        if already_paid:
            payout = await create_payroll_disbursement(db, company_id=company.id,
                payroll_calculation_id=calc.id, bank_account_id=bank.id, amount=already_paid,
                payment_date=date(2026,9,30), created_by=args['calculated_by'], request_key='original-paid')
            await generate_and_post_payroll_disbursement_journal(db, company_id=company.id,
                payroll_disbursement_id=payout.id, created_by=args['calculated_by'])
        evidence = await changed_evidence(db, company, args)
        application = await apply_sick_correction(db, company_id=company.id, original_sick_id=sick.id,
            insurance_evidence_id=evidence.id, correction_date=date(2026,10,26), request_key='increase',
            reason='Reviewed longer insurance service history', actor_id=args['calculated_by'])
        edge = await db.scalar(select(PayrollSickPayrollRevision).where(
            PayrollSickPayrollRevision.application_id==application.id))
        new_tax = await db.scalar(select(PayrollStatutoryResult).where(
            PayrollStatutoryResult.payroll_calculation_id==edge.replacement_calculation_id))
        assert edge.net_delta > 0
        difference = await create_payroll_disbursement(db, company_id=company.id,
            payroll_calculation_id=edge.replacement_calculation_id, bank_account_id=bank.id,
            payment_date=date(2026,10,26), created_by=args['calculated_by'], request_key='difference')
        assert difference.amount == new_tax.net_amount-already_paid
        await generate_and_post_payroll_disbursement_journal(db, company_id=company.id,
            payroll_disbursement_id=difference.id, created_by=args['calculated_by'])
        contract = await db.get(EmploymentContract, calc.employment_contract_id)
        balance = await employee_payroll_balance(db, company_id=company.id,
            employee_id=contract.employee_id, as_of=date(2026,10,26))
        assert balance['complete']
        assert balance['balances'][0]['balance'] == 0
        assert original_journal.status == 'posted'
        assert (await generate_and_post_payroll_journal_entry(db, company_id=company.id,
            payroll_calculation_id=edge.replacement_calculation_id, created_by=args['calculated_by'])).id == edge.journal_entry_id


async def test_continuation_correction_revises_whole_case_and_both_payroll_months(employee_engine):
    from datetime import datetime, UTC
    from sqlalchemy import text
    from app.models.leave_request import LeaveRequest
    from app.models.payroll_sick_leave import PayrollSickLeaveCalculation
    from app.services.payroll_sick_correction_service import request_sick_correction
    await install_revision_guards(employee_engine)
    await protect(employee_engine)
    async with AsyncSession(employee_engine, expire_on_commit=False) as db:
        company, args, first, september, _, _, _ = await setup(db)
        leave = LeaveRequest(company_id=company.id, employment_contract_id=first.employment_contract_id,
            leave_type='sick', start_date=date(2026,10,5), end_date=date(2026,10,9), status='approved',
            requested_by=args['calculated_by'], approved_by=args['calculated_by'], approved_at=datetime.now(UTC))
        db.add(leave)
        await db.flush()
        continued = await calculate_sick_from_history(db, **dict(args, leave_request_id=leave.id))
        october = await payroll_for_sick(db, company, continued, args['calculated_by'], extra_sources=(first,))
        await calculate_payroll_statutory_result(db, company_id=company.id,
            payroll_calculation_id=october.id, actor_user_id=args['calculated_by'])
        await generate_and_post_payroll_journal_entry(db, company_id=company.id,
            payroll_calculation_id=october.id, created_by=args['calculated_by'])
        evidence = await changed_evidence(db, company, args)
        pending = await request_sick_correction(db, company_id=company.id,
            original_calculation_id=continued.id, insurance_evidence_id=evidence.id,
            request_key='pending-continuation', reason='Reviewed whole case insurance service history',
            requested_by=args['calculated_by'])
        initial_pending = await request_sick_correction(db, company_id=company.id,
            original_calculation_id=first.id, insurance_evidence_id=evidence.id,
            request_key='pending-initial', reason='Reviewed whole case insurance service history',
            requested_by=args['calculated_by'])
        application = await apply_sick_correction(db, company_id=company.id, original_sick_id=continued.id,
            insurance_evidence_id=evidence.id, correction_date=date(2026,10,31), request_key='whole-case',
            reason='Reviewed whole case insurance service history', actor_id=args['calculated_by'])
        assert pending.status == initial_pending.status == 'applied'
        assert len(application.payroll_calculation_ids)==2
        assert set(application.source_replacements)=={str(first.id),str(continued.id)}
        new_continued = await db.get(PayrollSickLeaveCalculation, application.source_replacements[str(continued.id)])
        assert new_continued.employer_days==0
        assert new_continued.insurance_assessment['initial_calculation_id']==application.source_replacements[str(first.id)]
        assert new_continued.sick_pay_amount < continued.sick_pay_amount
        await db.execute(text('SET CONSTRAINTS ALL IMMEDIATE'))
        await db.execute(text('SET CONSTRAINTS ALL DEFERRED'))


async def test_sick_correction_revises_peer_contract_without_paying_it_twice(employee_engine):
    from app.models.employment_contract import EmploymentContract
    from app.models.payroll import PayrollPeriod, PayrollInput, PayrollCalculation, PayrollCalculationLine
    from app.services.payroll_correction_basis import make_basis, replay_basis
    from app.services.payroll_sick_financial_sources import recognized_sick_chain
    from sqlalchemy import text
    await install_revision_guards(employee_engine)
    await protect(employee_engine)
    async with AsyncSession(employee_engine, expire_on_commit=False) as db:
        company, args, sick, primary, _, bank, _ = await setup(db)
        original_contract = await db.get(EmploymentContract, primary.employment_contract_id)
        peer = EmploymentContract(company_id=company.id, employee_id=original_contract.employee_id,
            contract_number='SECONDARY', contract_type='indefinite', employment_kind='internal_secondary',
            start_date=date(2025,1,1), created_by=args['calculated_by'])
        db.add(peer)
        await db.flush()
        period = await db.get(PayrollPeriod, primary.payroll_period_id)
        payroll_input = PayrollInput(company_id=company.id, payroll_period_id=period.id,
            employment_contract_id=peer.id, created_by=args['calculated_by'])
        db.add(payroll_input)
        await db.flush()
        lines = [dict(line_no=1, line_type='manual_adjustment', description='Documented salary adjustment',
            quantity=D(1), rate=D(1000), amount=D(1000), currency_code='UAH')]
        facts = dict(rate_type='monthly', minimum_rate='8647', ordinary_worked_minutes=0,
            default_norm_minutes=10560, earnings=[dict(category='ordinary_salary', amount='1000')],
            adjustment='0', has_adjustment=False, period_start=period.start_date.isoformat(),
            period_end=period.end_date.isoformat(), rule_version='UA-4695-IX-2026-v1')
        basis = make_basis(company_id=company.id, period=period, payroll_input=payroll_input,
            salary_slices=[], facts=facts, lines=lines)
        gross, lines, assessment = replay_basis(basis,{})
        calc = PayrollCalculation(company_id=company.id, payroll_period_id=period.id,
            payroll_input_id=payroll_input.id, employment_contract_id=peer.id,
            gross_amount=gross, currency_code='UAH', calculated_by=args['calculated_by'],
            minimum_wage_assessment=dict(assessment,correction_basis=basis))
        db.add(calc)
        await db.flush()
        db.add_all([PayrollCalculationLine(company_id=company.id, payroll_calculation_id=calc.id, **line)
            for line in lines])
        await db.flush()
        await db.refresh(calc)
        await calculate_payroll_statutory_result(db, company_id=company.id,
            payroll_calculation_id=calc.id, actor_user_id=args['calculated_by'])
        journal = await generate_and_post_payroll_journal_entry(db, company_id=company.id,
            payroll_calculation_id=calc.id, created_by=args['calculated_by'])
        payout = await create_payroll_disbursement(db, company_id=company.id,
            payroll_calculation_id=calc.id, bank_account_id=bank.id, payment_date=date(2026,9,30),
            created_by=args['calculated_by'], request_key='peer-paid')
        await generate_and_post_payroll_disbursement_journal(db, company_id=company.id,
            payroll_disbursement_id=payout.id, created_by=args['calculated_by'])
        evidence = await changed_evidence(db,company,args)
        application = await apply_sick_correction(db,company_id=company.id,original_sick_id=sick.id,
            insurance_evidence_id=evidence.id,correction_date=date(2026,10,26),request_key='peer-correction',
            reason='Reviewed employee insurance history',actor_id=args['calculated_by'])
        assert len(application.payroll_calculation_ids)==2
        edge = await db.scalar(select(PayrollSickPayrollRevision).where(
            PayrollSickPayrollRevision.original_calculation_id==calc.id))
        assert edge.net_delta==0 and edge.journal_entry_id is None
        await recognized_sick_chain(db,company_id=company.id,
            calculation_id=edge.replacement_calculation_id,as_of=date(2026,10,26),error_type=ValueError)
        with pytest.raises(PayrollDisbursementSourceStateError,match='No positive remaining'):
            await create_payroll_disbursement(db,company_id=company.id,
                payroll_calculation_id=edge.replacement_calculation_id,bank_account_id=bank.id,
                payment_date=date(2026,10,26),created_by=args['calculated_by'],request_key='peer-double')
        assert journal.status=='posted'
        await db.execute(text('SET CONSTRAINTS ALL IMMEDIATE'))


async def test_corrected_pfu_funding_and_paid_tax_preserve_excess(employee_engine):
    from types import SimpleNamespace
    from app.services.payroll_sick_financial_sources import load_financial_snapshot
    from app.services.payroll_sick_settlement_service import sick_settlement_summary
    from app.services.payroll_sick_return_service import register_sick_return
    from app.services.payroll_insurance_funding_service import register_insurance_funding
    from app.services.payroll_tax_remittance_service import register_tax_remittance
    from app.services.payroll_tax_balance_service import payroll_tax_balances
    from test_payroll_insurance_funding import receipt, request as funding_request
    from test_payroll_tax_remittance import payment as tax_payment, request as tax_request
    from app.models.journal_entry_line import JournalEntryLine
    from app.services.accounting_posting import post_journal_entry
    from sqlalchemy import text
    await install_revision_guards(employee_engine)
    await protect(employee_engine)
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,args,sick,calc,tax,bank,_=await setup(db)
        calc=await payroll_for_sick(db,company,sick,args['calculated_by'],period_start=date(2026,10,1))
        await calculate_payroll_statutory_result(db,company_id=company.id,payroll_calculation_id=calc.id,actor_user_id=args['calculated_by'])
        await generate_and_post_payroll_journal_entry(db,company_id=company.id,payroll_calculation_id=calc.id,created_by=args['calculated_by'])
        actor=SimpleNamespace(id=args['calculated_by'])
        accounts=dict((await db.execute(select(Account.code,Account.id).where(Account.company_id==company.id))).all())
        snapshot=await load_financial_snapshot(db,company_id=company.id,calculation_id=calc.id)
        funding=JournalEntry(company_id=company.id,entry_date=date(2026,10,31),status='draft',created_by=actor.id)
        funding.lines=[JournalEntryLine(line_no=1,account_id=bank.accounting_account_id,debit=snapshot.insurer_gross,credit=0),
            JournalEntryLine(line_no=2,account_id=accounts['378'],debit=0,credit=snapshot.insurer_gross)]
        db.add(funding)
        await db.flush()
        await post_journal_entry(db,company.id,funding.id)
        await register_insurance_funding(db,company_id=company.id,
            data=funding_request(funding,bank,calc,snapshot.insurer_gross,'original-funding'),created_by=actor.id)
        tax_journal=await tax_payment(db,company,actor,accounts,bank,snapshot.personal_income_tax,day=date(2026,10,31))
        await register_tax_remittance(db,company_id=company.id,
            data=tax_request(tax_journal,bank,calc,snapshot.personal_income_tax,'original-tax'),created_by=actor.id)
        before=await payroll_tax_balances(db,company_id=company.id,as_of=date(2026,9,30))
        evidence=await changed_evidence(db,company,args)
        application=await apply_sick_correction(db,company_id=company.id,original_sick_id=sick.id,
            insurance_evidence_id=evidence.id,correction_date=date(2026,10,31),request_key='funding-correction',
            reason='Reviewed insurance history funding correction',actor_id=actor.id)
        edge=await db.scalar(select(PayrollSickPayrollRevision).where(PayrollSickPayrollRevision.original_calculation_id==calc.id))
        new_id=edge.replacement_calculation_id
        summary=await sick_settlement_summary(db,company_id=company.id,calculation_id=new_id,as_of=date(2026,10,31))
        assert summary['insurer_excess']>0 and summary['insurer_due']==0
        assert await payroll_tax_balances(db,company_id=company.id,as_of=date(2026,9,30))==before
        report=await payroll_tax_balances(db,company_id=company.id,as_of=date(2026,10,31))
        pit=next(row for row in report['rows'] if row['component']=='personal_income_tax' and row['payroll_calculation_id']==new_id)
        assert pit['overpaid_amount']>0 and pit['outstanding_amount']==0
        returned=JournalEntry(company_id=company.id,entry_date=date(2026,10,31),status='draft',
            created_by=actor.id,description='Confirmed PFU excess return')
        returned.lines=[JournalEntryLine(line_no=1,account_id=accounts['378'],debit=summary['insurer_excess'],credit=0),
            JournalEntryLine(line_no=2,account_id=bank.accounting_account_id,debit=0,credit=summary['insurer_excess'])]
        db.add(returned)
        await db.flush()
        await post_journal_entry(db,company.id,returned.id)
        await register_sick_return(db,company_id=company.id,calculation_id=new_id,journal_entry_id=returned.id,
            bank_account_id=bank.id,kind='insurer',request_key='pfu-return',
            document_reference='Confirmed PFU bank return and correction statement',actor_id=actor.id)
        final=await sick_settlement_summary(db,company_id=company.id,calculation_id=new_id,as_of=date(2026,10,31))
        assert final['insurer_excess']==final['insurer_due']==0
        assert final['employee_due']==summary['employee_due']
        await db.execute(text('SET CONSTRAINTS ALL IMMEDIATE'))


async def test_unposted_payroll_correction_posts_once_in_open_correction_period(employee_engine):
    from app.models.payroll import PayrollCalculation
    from app.services.payroll_sick_correction_application_service import SickCorrectionApplicationError
    from sqlalchemy import text
    await install_revision_guards(employee_engine)
    await protect(employee_engine)
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,args,sick,calc,_,bank,_=await setup(db,post=False)
        september=await db.scalar(select(AccountingPeriod).where(AccountingPeriod.company_id==company.id,
            AccountingPeriod.month==9))
        september.status="closed"
        september.is_locked=True
        await db.flush()
        evidence=await changed_evidence(db,company,args)
        params=dict(company_id=company.id,original_sick_id=sick.id,insurance_evidence_id=evidence.id,
            correction_date=date(2026,10,26),request_key='unposted',reason='Verified unposted payroll correction',
            actor_id=args['calculated_by'])
        with pytest.raises(SickCorrectionApplicationError,match='period end'):
            await apply_sick_correction(db,**dict(params,correction_date=date(2026,9,1)))
        app=await apply_sick_correction(db,**params)
        new_id=app.payroll_calculation_ids[0]
        journal=await db.scalar(select(JournalEntry).where(JournalEntry.payroll_calculation_id==new_id))
        assert journal.status=='posted' and journal.entry_date==date(2026,10,26)
        assert await db.scalar(select(JournalEntry.id).where(JournalEntry.payroll_calculation_id==calc.id)) is None
        assert await db.scalar(select(PayrollSickPayrollRevision.id).where(
            PayrollSickPayrollRevision.application_id==app.id)) is None
        payout=await create_payroll_disbursement(db,company_id=company.id,payroll_calculation_id=new_id,
            bank_account_id=bank.id,payment_date=date(2026,10,26),created_by=args['calculated_by'],request_key='unposted-pay')
        await generate_and_post_payroll_disbursement_journal(db,company_id=company.id,
            payroll_disbursement_id=payout.id,created_by=args['calculated_by'])
        await db.execute(text('SET CONSTRAINTS ALL IMMEDIATE'))


async def test_posting_failure_rolls_back_every_new_source_and_payroll(employee_engine,monkeypatch):
    from app.models.payroll import PayrollCalculation
    from app.models.payroll_sick_leave import PayrollSickLeaveCalculation
    from app.models.payroll_sick_correction_application import PayrollSickCorrectionApplication
    from app.services import payroll_sick_correction_application_service as service
    await install_revision_guards(employee_engine)
    await protect(employee_engine)
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,args,sick,calc,_,_,_=await setup(db)
        evidence=await changed_evidence(db,company,args)
        models=(PayrollSickLeaveCalculation,PayrollCalculation,PayrollSickCorrectionApplication,JournalEntry)
        before=[await db.scalar(select(func.count()).select_from(model)) for model in models]
        async def fail(*args,**kwargs):
            raise ValueError('Injected posting failure')
        monkeypatch.setattr(service,'post_journal_entry',fail)
        with pytest.raises(ValueError,match='Injected posting failure'):
            await service.apply_sick_correction(db,company_id=company.id,original_sick_id=sick.id,
                insurance_evidence_id=evidence.id,correction_date=date(2026,10,26),request_key='rollback',
                reason='Reviewed insurance history correction',actor_id=args['calculated_by'])
        after=[await db.scalar(select(func.count()).select_from(model)) for model in models]
        assert after==before


async def test_committed_correction_rejects_late_source_membership_and_truncate(employee_engine):
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError, DBAPIError
    from app.models.payroll_sick_leave import PayrollSickLeaveCalculationSource as Source
    await install_revision_guards(employee_engine)
    await protect(employee_engine)
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,args,sick,_,_,_,_=await setup(db)
        evidence=await changed_evidence(db,company,args)
        app=await apply_sick_correction(db,company_id=company.id,original_sick_id=sick.id,
            insurance_evidence_id=evidence.id,correction_date=date(2026,10,26),request_key='sealed',
            reason='Reviewed insurance history correction',actor_id=args['calculated_by'])
        source=await db.scalar(select(Source).where(Source.sick_leave_calculation_id==app.replacement_sick_id))
        values={column.name:getattr(source,column.name) for column in Source.__table__.columns if column.name!='id'}
        await db.commit()
        with pytest.raises(IntegrityError):
            async with db.begin_nested():
                db.add(Source(**values))
                await db.flush()
        with pytest.raises(DBAPIError,match='Sick history cannot be truncated'):
            async with db.begin_nested():
                await db.execute(text('TRUNCATE payroll_sick_leave_calculation_sources'))


async def test_concurrent_application_retry_commits_only_one_revision(employee_engine):
    import asyncio
    from app.models.payroll_sick_correction_application import PayrollSickCorrectionApplication
    await install_revision_guards(employee_engine)
    await protect(employee_engine)
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,args,sick,_,_,_,_=await setup(db)
        evidence=await changed_evidence(db,company,args)
        params=dict(company_id=company.id,original_sick_id=sick.id,insurance_evidence_id=evidence.id,
            correction_date=date(2026,10,26),request_key='concurrent',
            reason='Independently reviewed concurrent correction',actor_id=args['calculated_by'])
        await db.commit()
    async def apply_once():
        async with AsyncSession(employee_engine,expire_on_commit=False) as db:
            app=await apply_sick_correction(db,**params)
            await db.commit()
            return app.id
    first,second=await asyncio.wait_for(asyncio.gather(apply_once(),apply_once()),timeout=20)
    assert first==second
    async with AsyncSession(employee_engine) as db:
        assert await db.scalar(select(func.count()).select_from(PayrollSickCorrectionApplication))==1


async def test_mixed_sick_and_severance_keeps_protected_payment_history(employee_engine):
    from app.schemas.employment_event import EmploymentEventCreate
    from app.services.employment_event_service import create_employment_event
    from app.models.payroll_termination_severance import PayrollTerminationSeverance
    from app.services.payroll_termination_severance_lines import derive_severance_payroll_lines
    from app.services.payroll_severance_trusted_classification import persist_trusted_severance_snapshot
    from app.services.payroll_severance_payment_service import payment_protected_minimum
    from app.models.payroll import PayrollCalculationLine
    from sqlalchemy import text
    await install_revision_guards(employee_engine)
    await protect(employee_engine)
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,args,sick,_,_,bank,_=await setup(db,service_months=12)
        actor=args['calculated_by']
        from app.models.employment_contract import EmploymentContract
        contract=await db.get(EmploymentContract,sick.employment_contract_id)
        employee=await db.get(Employee,contract.employee_id)
        employee.hire_date=contract.start_date
        await db.flush()
        event=await create_employment_event(db,company_id=company.id,contract_id=sick.employment_contract_id,
            created_by=actor,data=EmploymentEventCreate(event_type='termination',effective_date=date(2026,10,20),
                order_number='TERM-SICK',order_date=date(2026,10,20),reason='Documented redundancy',
                request_key='term-sick',termination_basis=dict(ground='redundancy',labour_code_provision='40.1',
                    source_reference='Verified termination order')))
        async def severance(payroll_input,period):
            source=PayrollTerminationSeverance(company_id=company.id,payroll_input_id=payroll_input.id,
                employment_contract_id=sick.employment_contract_id,termination_event_id=event.id,
                revision=1,request_key='mixed-severance',termination_date=date(2026,10,20),
                order_number=event.order_number,policy_ground='redundancy',policy_category='average_months',
                labour_code_provision='40.1',source_reference='Verified termination order',multiplier=1,
                basis_as_of=date(2026,10,20),basis_reference='Verified average monthly earnings',
                base_amount=D(12000),gross_amount=D(12000),currency_code='UAH',created_by=actor)
            db.add(source)
            await db.flush()
            return (await derive_severance_payroll_lines(db,company_id=company.id,
                payroll_input=payroll_input,period=period))[0]
        calc=await payroll_for_sick(db,company,sick,actor,period_start=date(2026,10,1),severance_factory=severance)
        await persist_trusted_severance_snapshot(db,company_id=company.id,payroll_calculation_id=calc.id)
        await calculate_payroll_statutory_result(db,company_id=company.id,payroll_calculation_id=calc.id,actor_user_id=actor)
        await generate_and_post_payroll_journal_entry(db,company_id=company.id,payroll_calculation_id=calc.id,created_by=actor)
        payout=await create_payroll_disbursement(db,company_id=company.id,payroll_calculation_id=calc.id,
            bank_account_id=bank.id,payment_date=date(2026,10,31),created_by=actor,request_key='mixed-paid')
        await generate_and_post_payroll_disbursement_journal(db,company_id=company.id,payroll_disbursement_id=payout.id,created_by=actor)
        evidence=await changed_evidence(db,company,args)
        app=await apply_sick_correction(db,company_id=company.id,original_sick_id=sick.id,
            insurance_evidence_id=evidence.id,correction_date=date(2026,10,31),request_key='mixed-correction',
            reason='Verified corrected insurance service history',actor_id=actor)
        edge=await db.scalar(select(PayrollSickPayrollRevision).where(PayrollSickPayrollRevision.original_calculation_id==calc.id))
        preserved=await db.scalar(select(PayrollCalculationLine).where(
            PayrollCalculationLine.payroll_calculation_id==edge.replacement_calculation_id,
            PayrollCalculationLine.line_type=='severance'))
        assert preserved.amount==D(12000) and preserved.source_termination_severance_id
        difference=await create_payroll_disbursement(db,company_id=company.id,
            payroll_calculation_id=edge.replacement_calculation_id,bank_account_id=bank.id,
            payment_date=date(2026,10,31),created_by=actor,request_key='mixed-difference')
        assert difference.amount==edge.net_delta
        assert await payment_protected_minimum(db,company_id=company.id,payment=difference)==0
        await generate_and_post_payroll_disbursement_journal(db,company_id=company.id,
            payroll_disbursement_id=difference.id,created_by=actor)
        await db.execute(text('SET CONSTRAINTS ALL IMMEDIATE'))
