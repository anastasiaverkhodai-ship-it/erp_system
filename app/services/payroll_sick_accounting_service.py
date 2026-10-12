"""Reconcile payroll sick lines to the employer/insurer funding source."""
from datetime import timedelta
from decimal import Decimal as D
from sqlalchemy import select
from app.models.leave_request import LeaveRequest
from app.models.payroll import PayrollCalculationLine
from app.models.payroll_sick_leave import PayrollSickLeaveCalculation
from app.services.payroll_leave_allocation import allocate_leave_amount
from app.services.payroll_sick_allocation import DATED_ALLOCATION_VERSIONS, sick_period_amount


async def sick_funding_for_calculation(db, *, company_id, calculation, period):
    lines = list((await db.scalars(select(PayrollCalculationLine).where(
        PayrollCalculationLine.company_id == company_id,
        PayrollCalculationLine.payroll_calculation_id == calculation.id,
        PayrollCalculationLine.line_type == 'sick_pay'))).all())
    employer, insurer, seen = D(0), D(0), set()
    for line in lines:
        source_id = line.source_sick_leave_calculation_id
        if not source_id or source_id in seen:
            raise ValueError('Sick payroll line requires one unique funding source')
        seen.add(source_id)
        pair = (await db.execute(select(PayrollSickLeaveCalculation, LeaveRequest)
            .join(LeaveRequest, (LeaveRequest.company_id == PayrollSickLeaveCalculation.company_id) &
                  (LeaveRequest.id == PayrollSickLeaveCalculation.leave_request_id))
            .where(PayrollSickLeaveCalculation.company_id == company_id,
                   PayrollSickLeaveCalculation.id == source_id,
                   PayrollSickLeaveCalculation.employment_contract_id == calculation.employment_contract_id))).one_or_none()
        if pair is None:
            raise ValueError('Sick payroll source is missing or belongs to another contract')
        source, leave = pair
        if leave.status != 'approved' or leave.leave_type != 'sick' or line.currency_code != 'UAH':
            raise ValueError('Sick payroll source must be approved in UAH')
        days, total = sick_period_amount(source, leave, period.start_date, period.end_date)
        if D(days) != line.quantity or total != line.amount or days <= 0:
            raise ValueError('Sick payroll line does not reconcile to its dated funding source')
        if source.rule_version in DATED_ALLOCATION_VERSIONS:
            insurer_part = sum((D(x['amount']) for x in source.insurance_assessment['daily_allocation']
                if x['payer'] == 'insurer' and period.start_date.isoformat() <= x['date'] <= period.end_date.isoformat()), D(0))
            employer_part = total - insurer_part
        elif source.insurer_days == 0:
            employer_part, insurer_part = total, D(0)
        elif source.employer_days == 0:
            employer_part, insurer_part = D(0), total
        else:
            if (source.benefit_case_code != 'ORDINARY_TEMPORARY_INCAPACITY'
                    or source.employer_days != int(source.employer_days)):
                raise ValueError('Legacy mixed sick funding requires a documented daily allocation')
            employer_end = leave.start_date + timedelta(days=int(source.employer_days)-1)
            _, employer_part = allocate_leave_amount(start=leave.start_date, end=employer_end,
                period_start=period.start_date, period_end=period.end_date, amount=source.employer_amount)
            insurer_part = total - employer_part
        if employer_part < 0 or insurer_part < 0:
            raise ValueError('Sick financing does not reconcile to payroll allocation')
        employer += employer_part
        insurer += insurer_part
    if employer + insurer > calculation.gross_amount:
        raise ValueError('Sick funding exceeds payroll gross')
    return employer, insurer
