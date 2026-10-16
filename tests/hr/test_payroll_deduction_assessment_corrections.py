import os
from datetime import date
from decimal import Decimal as D
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession
from test_employee_foundation import employee_engine
from test_payroll_deduction_execution_postgresql import protect, seed, request
from app.models.payroll_deduction_execution import PayrollDeductionAssessmentClosure as Closure, PayrollWithholdingLine as Line
from app.models.accounting_period import AccountingPeriod
from app.schemas.payroll_deduction_execution import AssessmentCreate, AssessmentCorrectionCreate
from app.services.payroll_deduction_execution_service import correct_assessment, execute_payment, DeductionExecutionError, recipient_balances
pytestmark=[pytest.mark.asyncio,pytest.mark.skipif(os.getenv('RUN_POSTGRES_E2E')!='1',reason='Isolated PostgreSQL')]


async def test_correct_remaining_claim_keeps_paid_history_and_blocks_backdating(employee_engine):
    await protect(employee_engine)
    async with employee_engine.begin() as conn:
        await conn.execute(text('CREATE TRIGGER trg_pdac_immutable BEFORE INSERT OR UPDATE OR DELETE ON payroll_deduction_assessment_closures FOR EACH ROW EXECUTE FUNCTION payroll_guard_execution_record()'))
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,user,contract,calc,bank,beneficiary,claim=await seed(db)
        first=await execute_payment(db,company_id=company.id,data=request(calc,bank,'original'),actor_id=user.id)
        assert first.withheld_amount==D(200)
        replacement=AssessmentCreate(instruction_id=claim.instruction_id,beneficiary_id=beneficiary.id,
            rule_approval_id=claim.rule_approval_id,reference='Corrected remaining sum',request_key='replacement',
            amount=D(50),priority=1,priority_reference='Confirmed order',individual_limit_percentage=D(20),effective_from=date(2026,10,1))
        data=AssessmentCorrectionCreate(request_key='correction',effective_from=date(2026,10,1),reason='Accountant confirmed remainder',replacement=replacement)
        closure=await correct_assessment(db,company_id=company.id,assessment_id=claim.id,data=data,actor_id=user.id)
        assert (await correct_assessment(db,company_id=company.id,assessment_id=claim.id,data=data,actor_id=user.id)).id==closure.id
        assert claim.amount==D(1000)
        with pytest.raises(DeductionExecutionError,match='backdate'):
            await execute_payment(db,company_id=company.id,data=request(calc,bank,'past'),actor_id=user.id)
        db.add(AccountingPeriod(company_id=company.id,year=2026,month=10,start_date=date(2026,10,1),end_date=date(2026,10,31),status='open',is_locked=False));await db.flush()
        next_data=request(calc,bank,'next').model_copy(update={'payment_date':date(2026,10,1)})
        second=await execute_payment(db,company_id=company.id,data=next_data,actor_id=user.id)
        assert second.withheld_amount==D(50)
        assert (await db.scalar(select(Line).where(Line.batch_id==second.id))).assessment_id==closure.replacement_assessment_id
        assert (await recipient_balances(db,company_id=company.id,as_of=date(2026,9,30)))['recipients'][0]['balance']==D(200)
        assert (await recipient_balances(db,company_id=company.id,as_of=date(2026,10,1)))['recipients'][0]['balance']==D(250)
        await db.commit()
        with pytest.raises(DBAPIError,match='immutable'):
            async with db.begin_nested():
                await db.execute(text('DELETE FROM payroll_deduction_assessment_closures WHERE id=:id'),{'id':closure.id})


async def test_close_unpaid_claim_and_reject_changed_retry(employee_engine):
    await protect(employee_engine)
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,user,contract,calc,bank,beneficiary,claim=await seed(db)
        data=AssessmentCorrectionCreate(request_key='close',effective_from=date(2026,9,30),reason='Confirmed withdrawal')
        await correct_assessment(db,company_id=company.id,assessment_id=claim.id,data=data,actor_id=user.id)
        with pytest.raises(DeductionExecutionError,match='different data'):
            await correct_assessment(db,company_id=company.id,assessment_id=claim.id,data=data.model_copy(update={'reason':'changed'}),actor_id=user.id)
        batch=await execute_payment(db,company_id=company.id,data=request(calc,bank,'no-longer-due'),actor_id=user.id)
        assert batch.withheld_amount==0


async def test_payslip_revisions_after_partial_payment_and_reversal(employee_engine):
    from app.models.journal_entry import JournalEntry
    from app.models.payroll_payslip import PayrollPayslip
    from app.services.payroll_payslip_service import generate_payroll_payslip, get_payroll_payslip_by_calculation
    from app.services.accounting_reversal import reverse_journal_entry
    await protect(employee_engine)
    async with AsyncSession(employee_engine,expire_on_commit=False) as db:
        company,user,contract,calc,bank,beneficiary,claim=await seed(db)
        first=await execute_payment(db,company_id=company.id,data=request(calc,bank,'first'),actor_id=user.id)
        args=dict(company_id=company.id,payroll_calculation_id=calc.id,actor_user_id=user.id)
        original=await generate_payroll_payslip(db,**args)
        assert original.revision==1 and original.non_statutory_deduction_amount==D(200)
        second=await execute_payment(db,company_id=company.id,data=request(calc,bank,'second'),actor_id=user.id)
        updated=await generate_payroll_payslip(db,**args)
        assert updated.revision==2 and updated.non_statutory_deduction_amount==D(400)
        assert original.non_statutory_deduction_amount==D(200)
        assert (await generate_payroll_payslip(db,**args)).id==updated.id
        for batch in (first,second):
            journal=await db.scalar(select(JournalEntry).where(JournalEntry.payroll_disbursement_id==batch.disbursement_id))
            await reverse_journal_entry(db,company.id,journal.id,date(2026,9,30),user.id)
        latest=await generate_payroll_payslip(db,**args)
        assert latest.revision==3 and latest.non_statutory_deduction_amount==0
        assert (await get_payroll_payslip_by_calculation(db,company_id=company.id,payroll_calculation_id=calc.id)).id==latest.id
        assert len((await db.scalars(select(PayrollPayslip).where(PayrollPayslip.company_id==company.id))).all())==3
