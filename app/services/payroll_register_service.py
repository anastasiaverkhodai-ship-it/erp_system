"""Current settlements for a payroll period, with explicit missing-source states."""
from decimal import Decimal
from sqlalchemy import and_, select
from app.models.company import Company
from app.models.employment_contract import EmploymentContract
from app.models.journal_entry import JournalEntry
from app.models.payroll import PayrollPeriod, PayrollInput, PayrollCalculation
from app.models.payroll_statutory import PayrollStatutoryResult
from app.models.payroll_disbursement import PayrollDisbursement
from app.schemas.payroll_register import PayrollRegister, PayrollRegisterRow, PayrollRegisterAdvance
from app.services.payroll_period_service import PayrollPeriodNotFoundError, PayrollPeriodError


async def get_payroll_register(db, *, company_id: int, payroll_period_id: int):
    # Payroll writes take an exclusive company lock. Keep all reads in one
    # consistent business state without changing the caller's isolation level.
    company = await db.scalar(select(Company.id).where(Company.id == company_id)
                              .with_for_update(read=True))
    period = await db.scalar(select(PayrollPeriod).where(PayrollPeriod.company_id == company_id,
        PayrollPeriod.id == payroll_period_id).execution_options(populate_existing=True))
    if company is None or period is None:
        raise PayrollPeriodNotFoundError('Payroll period not found')
    from app.services.payroll_revision_service import current_calculation_filter
    records = (await db.execute(select(PayrollInput, EmploymentContract, PayrollCalculation,
                                      PayrollStatutoryResult, JournalEntry)
        .join(EmploymentContract, and_(EmploymentContract.company_id == company_id,
              EmploymentContract.id == PayrollInput.employment_contract_id))
        .outerjoin(PayrollCalculation, and_(PayrollCalculation.company_id == company_id,
                   PayrollCalculation.payroll_input_id == PayrollInput.id, current_calculation_filter()))
        .outerjoin(PayrollStatutoryResult, and_(PayrollStatutoryResult.company_id == company_id,
                   PayrollStatutoryResult.payroll_calculation_id == PayrollCalculation.id))
        .outerjoin(JournalEntry, and_(JournalEntry.company_id == company_id,
                   JournalEntry.payroll_calculation_id == PayrollCalculation.id,
                   JournalEntry.reversal_of_id.is_(None)))
        .where(PayrollInput.company_id == company_id, PayrollInput.payroll_period_id == period.id)
        .order_by(EmploymentContract.employee_id, PayrollInput.employment_contract_id, PayrollInput.id)
        .limit(10001).execution_options(populate_existing=True))).all()
    if len(records) > 10000:
        raise PayrollPeriodError('Payroll register exceeds 10000 inputs')
    calculation_ids = [calc.id for _, _, calc, _, _ in records if calc is not None]
    payouts = (await db.scalars(select(PayrollDisbursement).where(
        PayrollDisbursement.company_id == company_id,
        PayrollDisbursement.payroll_calculation_id.in_(calculation_ids))
        .execution_options(populate_existing=True))).all() if calculation_ids else []
    by_calculation = {}
    for payout in payouts:
        by_calculation.setdefault(payout.payroll_calculation_id, []).append(payout)
    from app.models.payroll_advance import PayrollAdvance
    from app.models.payroll_deduction_result import PayrollDeductionResult
    advances = (await db.execute(select(PayrollAdvance, JournalEntry)
        .outerjoin(JournalEntry, and_(JournalEntry.company_id == company_id,
            JournalEntry.payroll_advance_id == PayrollAdvance.id, JournalEntry.reversal_of_id.is_(None)))
        .where(PayrollAdvance.company_id == company_id, PayrollAdvance.payroll_period_id == period.id))).all()
    advance_by_contract = {}
    for advance, entry in advances:
        advance_by_contract[advance.employment_contract_id] = (advance, entry)
    deductions = (await db.execute(select(PayrollDeductionResult, JournalEntry)
        .outerjoin(JournalEntry, and_(JournalEntry.company_id == company_id,
            JournalEntry.payroll_deduction_result_id == PayrollDeductionResult.id,
            JournalEntry.reversal_of_id.is_(None)))
        .where(PayrollDeductionResult.company_id == company_id,
            PayrollDeductionResult.payroll_calculation_id.in_(calculation_ids)))).all() if calculation_ids else []
    deduction_by_calculation = {result.payroll_calculation_id: (result, entry) for result, entry in deductions}
    from app.models.payroll_deduction import PayrollDeductionInstruction
    instruction_contracts = set((await db.scalars(select(PayrollDeductionInstruction.employment_contract_id)
        .where(PayrollDeductionInstruction.company_id == company_id,
            PayrollDeductionInstruction.effective_from <= period.end_date,
            (PayrollDeductionInstruction.effective_to.is_(None) |
             (PayrollDeductionInstruction.effective_to >= period.end_date))))).all())
    rows = []
    for source, contract, calc, statutory, journal in records:
        issues = []
        if calc is None:
            issues.append('calculation_missing')
        elif statutory is None:
            issues.append('statutory_result_missing')
        status = str(getattr(journal.status, 'value', journal.status)) if journal else 'missing'
        if status != 'posted':
            issues.append('accrual_not_posted')
        currency = calc.currency_code if calc else None
        if statutory is not None and (statutory.currency_code != currency or statutory.gross_amount != calc.gross_amount):
            issues.append('statutory_result_mismatch')
        paid, reserved = Decimal(0), Decimal(0)
        for payout in by_calculation.get(calc.id if calc else None, []):
            if payout.currency_code != currency:
                issues.append('payout_currency_mismatch')
                continue
            if payout.reversed_on is not None or payout.cancelled_at is not None:
                continue
            if payout.confirmed_at is not None:
                paid += payout.amount
            else:
                reserved += payout.amount
        net = statutory.net_amount if statutory else None
        advance_paid = advance_reserved = deduction_posted = Decimal(0)
        advance_data = advance_by_contract.get(contract.id)
        if advance_data:
            advance, advance_entry = advance_data
            if currency is not None and advance.currency_code != currency:
                issues.append('advance_currency_mismatch')
            if advance_entry is not None and advance_entry.status == 'posted':
                advance_paid = advance.paid_amount
            elif advance_entry is None or advance_entry.status != 'reversed':
                advance_reserved = advance.paid_amount
        deduction_data = deduction_by_calculation.get(calc.id if calc else None)
        deduction_amount = Decimal(0)
        deduction_reversed = False
        if deduction_data:
            deduction, deduction_entry = deduction_data
            deduction_amount = deduction.deduction_amount
            if deduction.statutory_net_amount != net or deduction.currency_code != currency:
                issues.append('deduction_result_mismatch')
            if deduction_entry is not None and deduction_entry.status == 'posted':
                deduction_posted = deduction_amount
            elif deduction_entry is not None and deduction_entry.status == 'reversed':
                deduction_reversed = True
            elif deduction_amount:
                issues.append('deduction_not_posted')
        missing_deduction = contract.id in instruction_contracts and deduction_data is None
        if missing_deduction:
            issues.append('deduction_result_missing')
        final_payable = net - (Decimal(0) if deduction_reversed else deduction_amount) if net is not None and not missing_deduction else None
        balance = (net if status == 'posted' else Decimal(0)) - paid - advance_paid - deduction_posted if net is not None else None
        available = max(Decimal(0), final_payable - paid - reserved - advance_paid - advance_reserved) if final_payable is not None else None
        if balance is not None and balance < 0:
            issues.append('paid_exceeds_recognized_net')
        if final_payable is not None and paid + reserved + advance_paid + advance_reserved > final_payable:
            issues.append('reserved_exceeds_net')
        rows.append(PayrollRegisterRow(payroll_input_id=source.id,
            employment_contract_id=contract.id, employee_id=contract.employee_id,
            payroll_calculation_id=calc.id if calc else None, currency_code=currency,
            gross_amount=calc.gross_amount if calc else None,
            employee_withholding_amount=statutory.employee_withholding_amount if statutory else None,
            employer_contribution_amount=statutory.employer_contribution_amount if statutory else None,
            net_amount=net, accrual_status=status, paid_amount=paid, reserved_amount=reserved,
            advance_paid_amount=advance_paid, advance_reserved_amount=advance_reserved,
            deduction_amount=None if missing_deduction else deduction_amount, deduction_posted_amount=deduction_posted,
            final_payable_amount=final_payable, recognized_balance=balance, available_to_pay=available, issues=issues))
    return PayrollRegister(company_id=company_id, payroll_period_id=period.id,
        period_status=str(getattr(period.status, 'value', period.status)),
        advances=[PayrollRegisterAdvance(id=advance.id, employment_contract_id=advance.employment_contract_id,
            payment_date=advance.payment_date,currency_code=advance.currency_code,amount=advance.paid_amount,
            status=str(getattr(entry.status, 'value', entry.status)) if entry is not None else 'draft')
            for advance, entry in advances],
        complete=bool(rows) and all(not row.issues for row in rows), rows=rows)
