"""Current settlements for a payroll period, with explicit missing-source states."""
from decimal import Decimal
from sqlalchemy import and_, select
from app.models.company import Company
from app.models.employment_contract import EmploymentContract
from app.models.journal_entry import JournalEntry
from app.models.payroll import PayrollPeriod, PayrollInput, PayrollCalculation
from app.models.payroll_statutory import PayrollStatutoryResult
from app.models.payroll_disbursement import PayrollDisbursement
from app.schemas.payroll_register import PayrollRegister, PayrollRegisterRow
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
    records = (await db.execute(select(PayrollInput, EmploymentContract, PayrollCalculation,
                                      PayrollStatutoryResult, JournalEntry)
        .join(EmploymentContract, and_(EmploymentContract.company_id == company_id,
              EmploymentContract.id == PayrollInput.employment_contract_id))
        .outerjoin(PayrollCalculation, and_(PayrollCalculation.company_id == company_id,
                   PayrollCalculation.payroll_input_id == PayrollInput.id))
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
        balance = (net if status == 'posted' else Decimal(0)) - paid if net is not None else None
        available = max(Decimal(0), balance - reserved) if balance is not None else None
        if balance is not None and balance < 0:
            issues.append('paid_exceeds_recognized_net')
        if net is not None and paid + reserved > net:
            issues.append('reserved_exceeds_net')
        rows.append(PayrollRegisterRow(payroll_input_id=source.id,
            employment_contract_id=contract.id, employee_id=contract.employee_id,
            payroll_calculation_id=calc.id if calc else None, currency_code=currency,
            gross_amount=calc.gross_amount if calc else None,
            employee_withholding_amount=statutory.employee_withholding_amount if statutory else None,
            employer_contribution_amount=statutory.employer_contribution_amount if statutory else None,
            net_amount=net, accrual_status=status, paid_amount=paid, reserved_amount=reserved,
            recognized_balance=balance, available_to_pay=available, issues=issues))
    return PayrollRegister(company_id=company_id, payroll_period_id=period.id,
        period_status=str(getattr(period.status, 'value', period.status)),
        complete=bool(rows) and all(not row.issues for row in rows), rows=rows)
