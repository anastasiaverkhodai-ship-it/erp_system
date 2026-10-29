from datetime import date
from decimal import Decimal as D
from importlib.util import spec_from_file_location, module_from_spec
from unittest.mock import patch
from contextlib import ExitStack
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from alembic.operations import Operations
from alembic.migration import MigrationContext
from alembic import op
from test_employee_foundation import employee_engine
from test_payroll_leave_correction_financial_postgresql import setup, install
from app.models.payroll import PayrollCalculation
from app.models.time_attendance import AttendanceRecord
from app.schemas.payroll_leave_correction import LeaveCorrectionRequestCreate
from app.services.payroll_leave_correction_service import request_leave_correction, apply_leave_correction
from app.schemas.payroll_attendance_correction import AttendanceCorrectionCreate
from app.services.payroll_attendance_correction_service import request_attendance_correction, apply_attendance_correction, effective_attendance_changes, AttendanceCorrectionError
from app.services.payroll_disbursement_service import create_payroll_disbursement, generate_and_post_payroll_disbursement_journal
from app.services.payroll_sick_settlement_service import sick_settlement_summary
pytestmark=pytest.mark.asyncio


async def install_attendance(engine):
    await install(engine)
    spec=spec_from_file_location('attendance066','alembic/versions/13d4a7b8c066_attendance_financial_corrections.py')
    module=module_from_spec(spec);spec.loader.exec_module(module)
    async with engine.begin() as conn:
        def run(sync):
            with Operations.context(MigrationContext.configure(sync)), ExitStack() as stack:
                for name in ('drop_constraint','create_check_constraint','create_table'):
                    stack.enter_context(patch.object(op,name))
                module.upgrade()
        await conn.run_sync(run)


async def test_recall_then_reviewed_work_pays_only_salary_difference(employee_engine):
    await install_attendance(employee_engine)
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,user,reviewer,source,calc,bank,_=await setup(db,with_salary=True)
        payout=await create_payroll_disbursement(db,company_id=company.id,payroll_calculation_id=calc.id,
            bank_account_id=bank.id,payment_date=date(2026,9,30),created_by=user.id,request_key='original')
        await generate_and_post_payroll_disbursement_journal(db,company_id=company.id,
            payroll_disbursement_id=payout.id,created_by=user.id)
        args=dict(calculation_id=calc.id,correction_date=date(2026,10,28),request_key='work',
            reason='Confirmed work after documented recall',document_reference='Corrected timesheet order',
            days=[dict(work_date=date(2026,9,10),status='present',worked_minutes=480)])
        with pytest.raises(AttendanceCorrectionError,match='approved absence'):
            await request_attendance_correction(db,company_id=company.id,actor_id=user.id,
                data=AttendanceCorrectionCreate(**args))
        leave=LeaveCorrectionRequestCreate(target_kind='vacation',target_id=source.id,
            correction_date=date(2026,10,28),request_key='recall',reason='Documented recall after payment',
            order_reference='Recall order',consent_reference='Employee consent',operation='recall',
            effective_date=date(2026,9,10),retained_ranges=[dict(start=date(2026,9,1),end=date(2026,9,9))],
            recall_ground='accident_prevention',annual_continuity_reference='Separate uninterrupted annual period in schedule')
        req=await request_leave_correction(db,company_id=company.id,actor_id=user.id,data=leave)
        app=await apply_leave_correction(db,company_id=company.id,request_id=req.id,actor_id=reviewer.id)
        args['calculation_id']=app.payroll_calculation_ids[0]
        req=await request_attendance_correction(db,company_id=company.id,actor_id=user.id,
            data=AttendanceCorrectionCreate(**args))
        with pytest.raises(AttendanceCorrectionError,match='Independent'):
            await apply_attendance_correction(db,company_id=company.id,request_id=req.id,actor_id=user.id)
        app=await apply_attendance_correction(db,company_id=company.id,request_id=req.id,actor_id=reviewer.id)
        assert (await apply_attendance_correction(db,company_id=company.id,request_id=req.id,actor_id=reviewer.id)).id==app.id
        new=await db.get(PayrollCalculation,app.payroll_calculation_ids[0])
        assert new.gross_amount==D(12900) # 12000 salary + 900 retained leave
        state=await sick_settlement_summary(db,company_id=company.id,calculation_id=new.id,as_of=date(2026,10,28))
        assert state['employee_due']==D(693) # original 12000 gross already paid; net delta 900 * .77
        row=await db.scalar(select(AttendanceRecord).where(AttendanceRecord.company_id==company.id))
        assert row.worked_minutes==0 and row.status=='leave'
        projection=await effective_attendance_changes(db,company_id=company.id,contract_id=new.employment_contract_id)
        assert projection[date(2026,9,10)]['worked_minutes']==480
        from app.services.payroll_attendance_correction_service import attendance_read_views
        view=(await attendance_read_views(db,[row]))[0]
        assert view.worked_minutes==0 and view.effective_worked_minutes==480
        assert view.correction_application_id==app.id
        await db.execute(text('SET CONSTRAINTS ALL IMMEDIATE'))
        await db.execute(text('SET CONSTRAINTS ALL DEFERRED'))
        # A later evidence correction reduces worked time without rewriting the
        # paid source. Only a confirmed bank return can settle the overpayment.
        args.update(calculation_id=new.id, correction_date=date(2026,10,29), request_key='work-reduced',
            days=[dict(work_date=date(2026,9,10),status='absent',worked_minutes=0)])
        req2=await request_attendance_correction(db,company_id=company.id,actor_id=user.id,
            data=AttendanceCorrectionCreate(**args))
        app2=await apply_attendance_correction(db,company_id=company.id,request_id=req2.id,actor_id=reviewer.id)
        latest=await db.get(PayrollCalculation,app2.payroll_calculation_ids[0])
        assert latest.gross_amount==D(11900)
        state=await sick_settlement_summary(db,company_id=company.id,calculation_id=latest.id,as_of=date(2026,10,29))
        assert state['employee_overpayment']==D(77)
        historical=await effective_attendance_changes(db,company_id=company.id,
            contract_id=latest.employment_contract_id,as_of=date(2026,10,28))
        assert historical[date(2026,9,10)]['worked_minutes']==480
        from app.models.account import Account
        from app.models.journal_entry import JournalEntry
        from app.models.journal_entry_line import JournalEntryLine
        from app.services.accounting_posting import post_journal_entry
        from app.services.payroll_sick_return_service import register_sick_return
        payable=await db.scalar(select(Account.id).where(Account.company_id==company.id,Account.code=='66'))
        receipt=JournalEntry(company_id=company.id,entry_date=date(2026,10,29),
            status='draft',created_by=user.id,description='Confirmed attendance correction refund')
        receipt.lines=[JournalEntryLine(line_no=1,account_id=bank.accounting_account_id,debit=77,credit=0),
            JournalEntryLine(line_no=2,account_id=payable,debit=0,credit=77)]
        db.add(receipt);await db.flush();await post_journal_entry(db,company.id,receipt.id)
        await register_sick_return(db,company_id=company.id,calculation_id=latest.id,journal_entry_id=receipt.id,
            bank_account_id=bank.id,kind='employee',request_key='confirmed-refund',
            document_reference='Confirmed employee bank repayment',actor_id=user.id)
        state=await sick_settlement_summary(db,company_id=company.id,calculation_id=latest.id,as_of=date(2026,10,29))
        assert state['employee_overpayment']==0 and state['employee_due']==0
        await db.execute(text('SET CONSTRAINTS ALL IMMEDIATE'))


async def test_unposted_attendance_correction_posts_full_new_liability(employee_engine):
    await install_attendance(employee_engine)
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,user,reviewer,source,calc,bank,_=await setup(db,with_salary=True,post=False)
        db.add(AttendanceRecord(company_id=company.id,employment_contract_id=calc.employment_contract_id,
            work_date=date(2026,9,11),status='absent',worked_minutes=0,source='manual',created_by=user.id))
        await db.flush()
        request=await request_attendance_correction(db,company_id=company.id,actor_id=user.id,
            data=AttendanceCorrectionCreate(calculation_id=calc.id,correction_date=date(2026,10,29),
                request_key='unposted',reason='Confirmed missing work day after absence',
                document_reference='Approved corrected timesheet',
                days=[dict(work_date=date(2026,9,11),status='present',worked_minutes=480)]))
        app=await apply_attendance_correction(db,company_id=company.id,request_id=request.id,actor_id=reviewer.id)
        new=await db.get(PayrollCalculation,app.payroll_calculation_ids[0])
        assert new.gross_amount==13000
        from app.services.payroll_accounting_service import get_payroll_accounting_journal
        journal=await get_payroll_accounting_journal(db,company_id=company.id,payroll_calculation_id=new.id)
        assert journal.status=='posted' and journal.entry_date==date(2026,10,29)
        assert await get_payroll_accounting_journal(db,company_id=company.id,payroll_calculation_id=calc.id) is None
        state=await sick_settlement_summary(db,company_id=company.id,calculation_id=new.id,as_of=date(2026,10,29))
        assert state['employee_due']==D(10010)
        await db.execute(text('SET CONSTRAINTS ALL IMMEDIATE'))
