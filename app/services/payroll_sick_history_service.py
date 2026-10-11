"""Ordinary incapacity from reviewed service evidence and attributed earnings."""
from datetime import date, timedelta
from decimal import Decimal as D
from sqlalchemy import select
from app.models.employment_contract import EmploymentContract
from app.models.leave_request import LeaveRequest
from app.models.payroll_insurance_evidence import PayrollInsuranceEvidence
from app.models.payroll_sick_leave import PayrollSickLeaveCalculation
from app.services.payroll_mutation_guard import serialized_payroll_mutation
from app.services.payroll_insurance_evidence_service import get_insurance_evidence, PayrollInsuranceEvidenceError
from app.services.payroll_average_history_service import derive_leave_average_history
from app.services.payroll_sick_leave_service import (
    calculate_sick_leave_pay, get_sick_leave_calculation_for_leave,
    list_sick_leave_calculation_sources, PayrollSickLeaveDerivationError, PayrollSickLeaveConflictError,
)

RULE_CODE = 'UA_ORDINARY_INCAPACITY_EVIDENCE'
from app.services.payroll_sick_allocation import (
    ALLOCATION_VERSION, allocate_sick_days, daily_benefit, maximum_contribution_base, sick_period_amount,
)

LEGACY_RULE_VERSION = '1-2026-1105-1266'
RULE_VERSION = ALLOCATION_VERSION


def service_percentage(months, days=0, entitlement='standard'):
    if entitlement in {'chornobyl_1_3','veteran','rehabilitated'}:
        return D(100)
    if entitlement != 'standard' or months < 0 or not 0 <= days <= 29:
        raise PayrollSickLeaveDerivationError('Unsupported insurance service or entitlement')
    if months < 36:
        return D(50)
    if months < 60:
        return D(60)
    if months < 96 or (months == 96 and days == 0):
        return D(70)
    return D(100)


@serialized_payroll_mutation(PayrollSickLeaveConflictError)
async def calculate_sick_from_history(db, *, company_id, leave_request_id, insurance_evidence_id, calculated_by):
    saved = await get_sick_leave_calculation_for_leave(db,company_id=company_id,leave_request_id=leave_request_id)
    if saved:
        if saved.insurance_evidence_id != insurance_evidence_id or saved.rule_code != RULE_CODE:
            raise PayrollSickLeaveConflictError('Existing sick calculation has different evidence; use a correction')
        return saved
    evidence = await get_insurance_evidence(db,company_id=company_id,evidence_id=insurance_evidence_id,lock=True)
    if evidence.status != 'verified':
        raise PayrollInsuranceEvidenceError('Verified insurance evidence is required')
    leave = await db.scalar(select(LeaveRequest).where(LeaveRequest.company_id==company_id,LeaveRequest.id==leave_request_id))
    if leave is None or leave.status != 'approved' or leave.leave_type != 'sick':
        raise PayrollSickLeaveDerivationError('Approved sick leave is required')
    contract = await db.scalar(select(EmploymentContract).where(EmploymentContract.company_id==company_id,
        EmploymentContract.id==leave.employment_contract_id))
    if contract is None or contract.employee_id != evidence.employee_id:
        raise PayrollInsuranceEvidenceError('Insurance evidence belongs to another employee')
    if contract.employment_kind != 'primary':
        raise PayrollSickLeaveDerivationError('Secondary employment requires combined attributed insurance earnings')
    if not date(2026,1,1) <= evidence.case_start <= leave.start_date <= leave.end_date <= date(2026,12,31):
        raise PayrollSickLeaveDerivationError('Insurance case requires a verified 2026 rule window')
    prior = (await db.execute(select(PayrollSickLeaveCalculation,LeaveRequest)
        .join(LeaveRequest,(LeaveRequest.company_id==PayrollSickLeaveCalculation.company_id)&
                          (LeaveRequest.id==PayrollSickLeaveCalculation.leave_request_id))
        .join(PayrollInsuranceEvidence,(PayrollInsuranceEvidence.company_id==PayrollSickLeaveCalculation.company_id)&
                                     (PayrollInsuranceEvidence.id==PayrollSickLeaveCalculation.insurance_evidence_id))
        .where(PayrollSickLeaveCalculation.company_id==company_id,
               PayrollSickLeaveCalculation.employment_contract_id==contract.id,
               PayrollInsuranceEvidence.case_start==evidence.case_start)
        .order_by(LeaveRequest.start_date))).all()
    cursor = evidence.case_start
    for old, certificate in prior:
        if (certificate.start_date != cursor or certificate.end_date >= leave.start_date
            or certificate.status != 'approved' or old.insurance_evidence_id != evidence.id
            or old.rule_code != RULE_CODE or old.rule_version not in {RULE_VERSION, LEGACY_RULE_VERSION}
            or old.rule_version != prior[0][0].rule_version):
            raise PayrollSickLeaveConflictError('Continuation requires contiguous approved certificates with the same evidence')
        cursor = certificate.end_date + timedelta(days=1)
    if cursor != leave.start_date:
        raise PayrollSickLeaveConflictError('Continuation has a gap or lacks the initial calculated certificate')
    if prior:
        first = prior[0][0]
        source_rows = await list_sick_leave_calculation_sources(db,company_id=company_id,sick_leave_calculation_id=first.id)
        basis = dict(reference_period_start=first.reference_period_start,reference_period_end=first.reference_period_end,
            eligible_earnings=first.eligible_earnings,eligible_days=first.eligible_days,
            sources=[dict(source_type=s.source_type,source_id=s.source_id,source_period_start=s.source_period_start,
                source_period_end=s.source_period_end,earnings_amount=s.earnings_amount,
                eligible_days=s.eligible_days,source_reference=s.source_reference) for s in source_rows
                if s.source_type != 'insurance_service_evidence'])
    else:
        basis = await derive_leave_average_history(db,company_id=company_id,leave_request_id=leave.id)
    version = prior[0][0].rule_version if prior else RULE_VERSION
    if version == RULE_VERSION and not prior:
        # Imported sick earnings are explicitly attributed contribution-bearing
        # earnings. Enforce each month's statutory base before averaging.
        for source in basis['sources']:
            maximum = maximum_contribution_base(source['source_period_end'])
            if D(source['earnings_amount']) > maximum:
                source['earnings_amount'] = maximum
                source['source_reference'] = ('USC maximum applied; ' + (source.get('source_reference') or ''))[:255]
        basis['eligible_earnings'] = sum((D(s['earnings_amount']) for s in basis['sources']), D(0))
    percent = service_percentage(evidence.total_service_months,evidence.service_days,evidence.entitlement)
    limited = evidence.recent_service_months < 6
    days = (leave.end_date-leave.start_date).days+1
    employer_days = max(0,(min(leave.end_date,evidence.case_start+timedelta(days=4))-leave.start_date).days+1)
    insurer_days = days-employer_days
    # High-income cases require monthly ceiling allocation across certificates
    # and jobs. Do not silently apply a daily ceiling to the wrong month.
    average = D(basis['eligible_earnings'])/D(basis['eligible_days'])
    maximum_reference_base = D(160000) if evidence.case_start.month == 1 else D(172940)
    if version == LEGACY_RULE_VERSION and not limited and (average > maximum_reference_base/D('30.44') or average*percent/100*31 > maximum_reference_base):
        raise PayrollSickLeaveDerivationError('High-income sick pay requires monthly maximum-base allocation')
    allocation = None
    if version == RULE_VERSION:
        daily, maximum_reference_base = daily_benefit(earnings=basis['eligible_earnings'],
            days=basis['eligible_days'], percent=percent, reference_end=basis['reference_period_end'], limited=limited)
        # Count prior ordinary benefits for this employee, including a former
        # primary contract in the same month. Later saved certificates require
        # a correction rather than retroactively consuming their ceiling.
        other_rows = (await db.execute(select(PayrollSickLeaveCalculation, LeaveRequest)
            .join(LeaveRequest, (LeaveRequest.company_id == PayrollSickLeaveCalculation.company_id) &
                  (LeaveRequest.id == PayrollSickLeaveCalculation.leave_request_id))
            .join(EmploymentContract, (EmploymentContract.company_id == PayrollSickLeaveCalculation.company_id) &
                  (EmploymentContract.id == PayrollSickLeaveCalculation.employment_contract_id))
            .where(PayrollSickLeaveCalculation.company_id == company_id,
                   EmploymentContract.employee_id == evidence.employee_id,
                   LeaveRequest.status == 'approved',
                   LeaveRequest.end_date >= leave.start_date.replace(day=1),
                   LeaveRequest.start_date <= (leave.end_date.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)))).all()
        spent = {}
        for previous, certificate in other_rows:
            if certificate.end_date >= leave.start_date:
                raise PayrollSickLeaveConflictError('Later or overlapping sick calculation requires a correction before allocating monthly limits')
            if previous.benefit_case_code != 'ORDINARY_TEMPORARY_INCAPACITY':
                raise PayrollSickLeaveConflictError('Other benefit cases require a combined monthly allocation')
            month = leave.start_date.replace(day=1)
            while month <= leave.end_date:
                next_month = (month.replace(day=28) + timedelta(days=4)).replace(day=1)
                _, used = sick_period_amount(previous, certificate, month, next_month - timedelta(days=1))
                key = month.strftime('%Y-%m')
                spent[key] = spent.get(key, D(0)) + used
                month = next_month
        allocation = allocate_sick_days(start=leave.start_date, end=leave.end_date,
            case_start=evidence.case_start, daily=daily, maximum=maximum_reference_base, previous=spent)
    sources = list(basis['sources'])+[dict(source_type='insurance_service_evidence',source_id=evidence.id,
        source_reference=evidence.document_reference[:255],earnings_amount=D(0),eligible_days=D(0))]
    row = await calculate_sick_leave_pay(db,company_id=company_id,leave_request_id=leave.id,
        benefit_case_code='ORDINARY_TEMPORARY_INCAPACITY',reference_period_start=basis['reference_period_start'],
        reference_period_end=basis['reference_period_end'],eligible_earnings=basis['eligible_earnings'],
        eligible_days=basis['eligible_days'],insurance_service_months=evidence.total_service_months,
        benefit_percent=percent,limited_service_rule_applied=limited,employer_days=D(employer_days),
        insurer_days=D(insurer_days),rule_code=RULE_CODE,rule_version=version,calculated_by=calculated_by,sources=sources)
    if allocation is not None:
        row.daily_benefit_amount = daily
        row.employer_amount = sum((D(e['amount']) for e in allocation if e['payer'] == 'employer'), D(0))
        row.insurer_amount = sum((D(e['amount']) for e in allocation if e['payer'] == 'insurer'), D(0))
        row.sick_pay_amount = row.employer_amount + row.insurer_amount
    row.insurance_evidence_id = evidence.id
    row.insurance_assessment = dict(rule_version=version,case_start=evidence.case_start.isoformat(),
        total_service_months=evidence.total_service_months,service_days=evidence.service_days,
        recent_service_months=str(evidence.recent_service_months),entitlement=evidence.entitlement,
        entitlement_reference=evidence.entitlement_reference,benefit_percent=str(percent),limited_service=limited,
        daily_limit='284.07' if limited else None,reviewed_by=evidence.reviewed_by,
        reviewed_at=evidence.reviewed_at.isoformat(),document_reference=evidence.document_reference,
        initial_calculation_id=prior[0][0].id if prior else row.id,
        sources=['https://zakon.rada.gov.ua/laws/show/1105-14',
                 'https://zakon.rada.gov.ua/laws/show/1266-2001-%D0%BF'])
    if allocation is not None:
        row.insurance_assessment.update(daily_allocation=allocation, monthly_maximum=str(maximum_reference_base))
    await db.flush()
    return row
