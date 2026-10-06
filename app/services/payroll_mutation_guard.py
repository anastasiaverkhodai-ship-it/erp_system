"""Serialize payroll source changes with period finalization per company."""
from datetime import date
from functools import wraps
from inspect import signature

from sqlalchemy import select
from app.models.company import Company
from app.models.payroll import PayrollInput, PayrollPeriod, PayrollPeriodStatus


def serialized_payroll_mutation(error_type):
    """Take the shared company lock before any source/period locks or reads."""
    def decorate(function):
        call_signature = signature(function)

        @wraps(function)
        async def guarded(*args, **kwargs):
            values = call_signature.bind(*args, **kwargs).arguments
            db = values.get('db', values.get('session'))
            company_id = values['company_id']
            company = await db.scalar(
                select(Company.id).where(Company.id == company_id).with_for_update()
            )
            if company is None:
                raise error_type('Company not found')
            return await function(*args, **kwargs)
        return guarded
    return decorate


async def ensure_payroll_source_editable(db, *, company_id, date_from,
                                        date_to=None, contract_id=None,
                                        error_type=ValueError):
    """A finalized snapshot requires a correction, never silent source editing."""
    query = select(PayrollPeriod.id).where(
        PayrollPeriod.company_id == company_id,
        PayrollPeriod.status == PayrollPeriodStatus.FINALIZED,
        PayrollPeriod.start_date <= (date_to or date.max),
        PayrollPeriod.end_date >= date_from,
    )
    if contract_id is not None:
        query = query.join(PayrollInput, PayrollInput.payroll_period_id == PayrollPeriod.id).where(
            PayrollInput.company_id == company_id,
            PayrollInput.employment_contract_id == contract_id,
        )
    if await db.scalar(query.limit(1)) is not None:
        raise error_type('Source overlaps finalized payroll; use a payroll correction')


async def lock_payroll_journal_company(db, company_id, journal_entry_id):
    """Also cover direct journal endpoints, using the same lock ordering."""
    from app.models.journal_entry import JournalEntry
    source = (await db.execute(select(
        JournalEntry.payroll_calculation_id, JournalEntry.payroll_disbursement_id,
        JournalEntry.payroll_advance_id, JournalEntry.payroll_deduction_result_id
    ).where(JournalEntry.company_id == company_id,
            JournalEntry.id == journal_entry_id))).first()
    if source is not None and any(value is not None for value in source):
        await db.scalar(select(Company.id).where(Company.id == company_id).with_for_update())


async def require_posted_payroll_accrual(db, *, company_id, disbursement, error_type):
    from app.models.journal_entry import JournalEntry, JournalEntryStatus
    if disbursement.cancelled_at is not None:
        raise error_type('Cancelled payroll disbursement cannot be posted')
    accrual = await db.scalar(select(JournalEntry).where(
        JournalEntry.company_id == company_id,
        JournalEntry.payroll_calculation_id == disbursement.payroll_calculation_id,
        JournalEntry.reversal_of_id.is_(None),
        JournalEntry.status == JournalEntryStatus.POSTED,
    ).execution_options(populate_existing=True))
    if accrual is None:
        raise error_type('Post payroll accrual before its disbursement')
    from app.models.payroll_deduction_result import PayrollDeductionResult
    deduction = await db.scalar(select(PayrollDeductionResult).where(
        PayrollDeductionResult.company_id == company_id,
        PayrollDeductionResult.payroll_calculation_id == disbursement.payroll_calculation_id))
    if deduction is not None and deduction.deduction_amount > 0:
        posting = await db.scalar(select(JournalEntry).where(
            JournalEntry.company_id == company_id,
            JournalEntry.payroll_deduction_result_id == deduction.id,
            JournalEntry.reversal_of_id.is_(None)))
        if posting is not None and posting.status == JournalEntryStatus.REVERSED:
            reversal_date = await db.scalar(select(JournalEntry.entry_date).where(
                JournalEntry.company_id == company_id, JournalEntry.reversal_of_id == posting.id,
                JournalEntry.status == JournalEntryStatus.POSTED))
            if reversal_date is None or reversal_date > disbursement.payment_date:
                raise error_type('Disbursement cannot precede deduction reversal')
        elif posting is None or posting.status != JournalEntryStatus.POSTED or posting.entry_date > disbursement.payment_date:
            raise error_type('Post payroll deductions before the final disbursement')
    if disbursement.payment_date < accrual.entry_date:
        raise error_type('Final payroll disbursement cannot predate accrual; use an advance')


async def ensure_no_posted_payroll_disbursement(db, *, company_id, calculation_id, error_type):
    from app.models.journal_entry import JournalEntry, JournalEntryStatus
    from app.models.payroll_disbursement import PayrollDisbursement
    payout = await db.scalar(select(JournalEntry.id).join(PayrollDisbursement,
        JournalEntry.payroll_disbursement_id == PayrollDisbursement.id).where(
        JournalEntry.company_id == company_id,
        PayrollDisbursement.company_id == company_id,
        PayrollDisbursement.payroll_calculation_id == calculation_id,
        JournalEntry.reversal_of_id.is_(None),
        JournalEntry.status == JournalEntryStatus.POSTED,
    ).limit(1))
    if payout is not None:
        raise error_type('Reverse payroll disbursement before reversing its accrual')
    from app.models.payroll_deduction_result import PayrollDeductionResult
    deduction = await db.scalar(select(JournalEntry.id).join(PayrollDeductionResult,
        PayrollDeductionResult.id == JournalEntry.payroll_deduction_result_id).where(
            JournalEntry.company_id == company_id, PayrollDeductionResult.company_id == company_id,
            PayrollDeductionResult.payroll_calculation_id == calculation_id,
            JournalEntry.reversal_of_id.is_(None), JournalEntry.status == JournalEntryStatus.POSTED).limit(1))
    if deduction is not None:
        raise error_type('Reverse payroll deductions before reversing their accrual')
