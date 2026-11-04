from datetime import date
from importlib.util import spec_from_file_location,module_from_spec
from alembic.operations import Operations
from alembic.migration import MigrationContext
import pytest
from sqlalchemy import select,text
from sqlalchemy.ext.asyncio import AsyncSession
from test_employee_foundation import employee_engine
from test_payroll_attendance_correction_postgresql import install_attendance
from test_payroll_sick_application_postgresql import setup
from app.models.payroll import PayrollCalculation
from app.models.payroll_sick_leave import PayrollSickLeaveCalculation
from app.models.payroll_insurance_evidence import PayrollInsuranceEvidence
from app.models.leave_request import LeaveRequest
from app.schemas.payroll_attendance_correction import AttendanceCorrectionCreate
from app.services.payroll_attendance_correction_service import request_attendance_correction,apply_attendance_correction

pytestmark=pytest.mark.asyncio

async def install_calendar(engine):
    await install_attendance(engine)
    spec=spec_from_file_location('calendar069','alembic/versions/13d4a7b8c069_reviewed_sick_calendars.py')
    module=module_from_spec(spec);spec.loader.exec_module(module)
    async with engine.begin() as conn:
        def run(sync):
            with Operations.context(MigrationContext.configure(sync)):module.upgrade()
        await conn.run_sync(run)
        # Apply the current multi-contract review guard without unrelated schema changes.
        import ast
        from pathlib import Path
        tree=ast.parse(Path('alembic/versions/p13exec261103_severance_financial_execution.py').read_text())
        upgrade=next(node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name=='upgrade')
        sql=next(node.args[0].value for node in ast.walk(upgrade) if isinstance(node,ast.Call)
            and node.args and isinstance(node.args[0],ast.Constant) and isinstance(node.args[0].value,str)
            and 'CREATE OR REPLACE FUNCTION erp_sick_calendar_review()' in node.args[0].value)
        await conn.execute(text(sql))

async def test_shortened_certificate_preserves_order_and_recalculates_paid_payroll(employee_engine):
    await install_calendar(employee_engine)
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,args,sick,calc,tax,bank,journal=await setup(db)
        leave=await db.get(LeaveRequest,sick.leave_request_id)
        evidence=await db.get(PayrollInsuranceEvidence,sick.insurance_evidence_id)
        original_dates=(leave.start_date,leave.end_date)
        request=await request_attendance_correction(db,company_id=company.id,actor_id=args['calculated_by'],
            data=AttendanceCorrectionCreate(calculation_id=calc.id,correction_date=date(2026,10,31),
                request_key='shortened-calendar',reason='Corrected medical certificate duration',
                document_reference='Verified corrected electronic certificate',sick_calendar=dict(
                    original_sick_id=sick.id,insurance_evidence_id=evidence.id,
                    start_date=leave.start_date,end_date=date(2026,9,30),
                    medical_document_reference='Corrected medical certificate reference')))
        app=await apply_attendance_correction(db,company_id=company.id,request_id=request.id,actor_id=evidence.reviewed_by)
        assert (await apply_attendance_correction(db,company_id=company.id,request_id=request.id,actor_id=evidence.reviewed_by)).id==app.id
        new_sick=await db.scalar(select(PayrollSickLeaveCalculation).where(PayrollSickLeaveCalculation.supersedes_id==sick.id))
        assert new_sick.sick_days==3
        assert new_sick.sick_pay_amount==240
        assert (leave.start_date,leave.end_date)==original_dates
        new=await db.get(PayrollCalculation,app.payroll_calculation_ids[0])
        assert new.gross_amount==calc.gross_amount
        from app.services.payroll_leave_correction_sources import leave_read_view
        view=await leave_read_view(db,leave)
        assert view.effective_leave_dates==[date(2026,9,28),date(2026,9,29),date(2026,9,30)]
        await db.execute(text('SET CONSTRAINTS ALL IMMEDIATE'))

async def test_cross_month_calendar_removes_paid_days_and_keeps_payments(employee_engine):
    from test_payroll_sick_payroll_revision_postgresql import payroll_for_sick
    from app.services.payroll_statutory_service import calculate_payroll_statutory_result
    from app.services.payroll_accounting_service import generate_and_post_payroll_journal_entry
    from app.services.payroll_disbursement_service import create_payroll_disbursement,generate_and_post_payroll_disbursement_journal
    from app.services.payroll_sick_settlement_service import sick_settlement_summary
    from app.services.payroll_revision_service import current_calculation_filter
    await install_calendar(employee_engine)
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,args,sick,september,tax,bank,journal=await setup(db)
        october=await payroll_for_sick(db,company,sick,args['calculated_by'],period_start=date(2026,10,1))
        await calculate_payroll_statutory_result(db,company_id=company.id,payroll_calculation_id=october.id,actor_user_id=args['calculated_by'])
        await generate_and_post_payroll_journal_entry(db,company_id=company.id,payroll_calculation_id=october.id,created_by=args['calculated_by'])
        payment=await create_payroll_disbursement(db,company_id=company.id,payroll_calculation_id=october.id,
            bank_account_id=bank.id,payment_date=date(2026,10,31),created_by=args['calculated_by'],request_key='october-paid')
        await generate_and_post_payroll_disbursement_journal(db,company_id=company.id,payroll_disbursement_id=payment.id,created_by=args['calculated_by'])
        evidence=await db.get(PayrollInsuranceEvidence,sick.insurance_evidence_id)
        request=await request_attendance_correction(db,company_id=company.id,actor_id=args['calculated_by'],
            data=AttendanceCorrectionCreate(calculation_id=september.id,correction_date=date(2026,10,31),
                request_key='cross-month-calendar',reason='Corrected medical certificate duration',
                document_reference='Verified corrected electronic certificate',sick_calendar=dict(
                    original_sick_id=sick.id,insurance_evidence_id=evidence.id,
                    start_date=date(2026,9,28),end_date=date(2026,9,30),
                    medical_document_reference='Corrected medical certificate reference')))
        await apply_attendance_correction(db,company_id=company.id,request_id=request.id,actor_id=evidence.reviewed_by)
        current=await db.scalar(select(PayrollCalculation).where(PayrollCalculation.payroll_input_id==october.payroll_input_id,current_calculation_filter()))
        assert current.gross_amount==october.gross_amount-320
        state=await sick_settlement_summary(db,company_id=company.id,calculation_id=current.id,as_of=date(2026,10,31))
        assert state['employee_overpayment']>0 and state['employee_due']==0
        assert payment.cancelled_at is None and payment.reversed_on is None
        await db.execute(text('SET CONSTRAINTS ALL IMMEDIATE'))

async def test_shortened_sick_and_actual_work_settle_together(employee_engine):
    from decimal import Decimal
    from app.services.payroll_disbursement_service import create_payroll_disbursement,generate_and_post_payroll_disbursement_journal
    from app.services.payroll_sick_settlement_service import sick_settlement_summary
    from app.services.payroll_attendance_correction_service import effective_attendance_changes
    await install_calendar(employee_engine)
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,args,sick,calc,tax,bank,journal=await setup(db,with_salary=True)
        payment=await create_payroll_disbursement(db,company_id=company.id,payroll_calculation_id=calc.id,
            bank_account_id=bank.id,payment_date=date(2026,9,30),created_by=args['calculated_by'],request_key='original-paid')
        await generate_and_post_payroll_disbursement_journal(db,company_id=company.id,payroll_disbursement_id=payment.id,created_by=args['calculated_by'])
        evidence=await db.get(PayrollInsuranceEvidence,sick.insurance_evidence_id)
        request=await request_attendance_correction(db,company_id=company.id,actor_id=args['calculated_by'],
            data=AttendanceCorrectionCreate(calculation_id=calc.id,correction_date=date(2026,10,31),
                request_key='calendar-and-work',reason='Medical certificate shortened and work confirmed',
                document_reference='Verified medical certificate and corrected timesheet',
                days=[dict(work_date=date(2026,9,30),status='present',worked_minutes=480)],
                sick_calendar=dict(original_sick_id=sick.id,insurance_evidence_id=evidence.id,
                    start_date=date(2026,9,28),end_date=date(2026,9,29),
                    medical_document_reference='Verified corrected electronic medical certificate')))
        app=await apply_attendance_correction(db,company_id=company.id,request_id=request.id,actor_id=evidence.reviewed_by)
        new=await db.get(PayrollCalculation,app.payroll_calculation_ids[0])
        assert new.gross_amount==calc.gross_amount+920
        state=await sick_settlement_summary(db,company_id=company.id,calculation_id=new.id,as_of=date(2026,10,31))
        assert state['employee_due']==Decimal('708.40') and state['employee_overpayment']==0
        work=await effective_attendance_changes(db,company_id=company.id,contract_id=calc.employment_contract_id)
        assert work[date(2026,9,30)]['worked_minutes']==480
        await db.execute(text('SET CONSTRAINTS ALL IMMEDIATE'))

async def test_corrected_zero_work_inside_sick_calendar_remains_classified_history(employee_engine):
    from app.models.payroll import PayrollPeriod
    from app.services.payroll_average_history_service import _erp_vacation_source
    await install_calendar(employee_engine)
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,args,sick,calc,tax,bank,journal=await setup(db,with_salary=True)
        evidence=await db.get(PayrollInsuranceEvidence,sick.insurance_evidence_id)
        request=await request_attendance_correction(db,company_id=company.id,actor_id=args['calculated_by'],
            data=AttendanceCorrectionCreate(calculation_id=calc.id,correction_date=date(2026,10,31),
                request_key='classified-zero-work',reason='Confirm zero work during corrected medical absence',
                document_reference='Verified medical absence and timesheet',
                days=[dict(work_date=date(2026,9,30),status='absent',worked_minutes=0)],
                sick_calendar=dict(original_sick_id=sick.id,insurance_evidence_id=evidence.id,
                    start_date=date(2026,9,28),end_date=date(2026,9,30),
                    medical_document_reference='Verified corrected electronic medical certificate')))
        app=await apply_attendance_correction(db,company_id=company.id,request_id=request.id,actor_id=evidence.reviewed_by)
        new=await db.get(PayrollCalculation,app.payroll_calculation_ids[0])
        period=await db.get(PayrollPeriod,new.payroll_period_id)
        from datetime import datetime,timezone
        period.status='finalized'
        period.finalized_by=args['calculated_by']
        period.finalized_at=datetime.now(timezone.utc)
        await db.flush()
        source=await _erp_vacation_source(db,company_id=company.id,contract_id=new.employment_contract_id,
            calculation=new,period=period,month=date(2026,9,1),month_end=date(2026,9,30),purpose='sick')
        assert source['eligible_days']==27
        assert source['earnings_amount']==11000
        await db.execute(text('SET CONSTRAINTS ALL IMMEDIATE'))


async def test_medical_review_covers_same_employee_secondary_contract(employee_engine):
    from uuid import uuid4
    from app.models.employment_contract import EmploymentContract
    from app.models.payroll_opening import PayrollEarningsHistory
    from app.services.payroll_sick_history_service import calculate_sick_from_history
    await install_calendar(employee_engine)
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,args,sick,calc,tax,bank,journal=await setup(db)
        original_contract=await db.get(EmploymentContract,calc.employment_contract_id)
        values={c.name:getattr(original_contract,c.name) for c in EmploymentContract.__table__.columns
            if c.name not in {'id','created_at','updated_at'}}
        values.update(contract_number=uuid4().hex,employment_kind='internal_secondary',work_arrangement='part_time')
        peer=EmploymentContract(**values);db.add(peer);await db.flush()
        histories=(await db.scalars(select(PayrollEarningsHistory).where(
            PayrollEarningsHistory.company_id==company.id,PayrollEarningsHistory.employment_contract_id==original_contract.id))).all()
        for row in histories:
            values={c.name:getattr(row,c.name) for c in PayrollEarningsHistory.__table__.columns
                if c.name not in {'id','created_at','updated_at'}}
            values['employment_contract_id']=peer.id
            db.add(PayrollEarningsHistory(**values))
        leave=await db.get(LeaveRequest,sick.leave_request_id)
        values={c.name:getattr(leave,c.name) for c in LeaveRequest.__table__.columns
            if c.name not in {'id','created_at','updated_at'}}
        values['employment_contract_id']=peer.id
        peer_leave=LeaveRequest(**values);db.add(peer_leave);await db.flush()
        peer_sick=await calculate_sick_from_history(db,**{**args,'leave_request_id':peer_leave.id})
        from app.models.payroll import PayrollPeriod
        from test_payroll_sick_payroll_revision_postgresql import payroll_for_sick
        await payroll_for_sick(db,company,peer_sick,args['calculated_by'],
            existing_period=await db.get(PayrollPeriod,calc.payroll_period_id))

        evidence=await db.get(PayrollInsuranceEvidence,sick.insurance_evidence_id)
        request=await request_attendance_correction(db,company_id=company.id,actor_id=args['calculated_by'],
            data=AttendanceCorrectionCreate(calculation_id=calc.id,correction_date=date(2026,10,31),
                request_key='medical-peer-contract',reason='Correct medical calendar and recalculate shared insurance case',
                document_reference='Verified corrected certificate across employment contracts',
                sick_calendar=dict(original_sick_id=sick.id,insurance_evidence_id=evidence.id,
                    start_date=date(2026,9,28),end_date=date(2026,9,30),
                    medical_document_reference='Verified corrected electronic medical certificate')))
        await apply_attendance_correction(db,company_id=company.id,request_id=request.id,actor_id=evidence.reviewed_by)
        revised=await db.scalar(select(PayrollSickLeaveCalculation).where(
            PayrollSickLeaveCalculation.company_id==company.id,PayrollSickLeaveCalculation.supersedes_id==peer_sick.id))
        assert revised is not None and revised.employment_contract_id==peer.id
        assert revised.sick_days==peer_sick.sick_days
        await db.execute(text('SET CONSTRAINTS ALL IMMEDIATE'))
