"""Ordinary incapacity from reviewed service evidence and attributed earnings."""
from datetime import date, timedelta
from decimal import Decimal as D
from sqlalchemy import select
from app.services.payroll_sick_revision_service import current_sick_calculation_filter
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
    ALLOCATION_VERSION, FIRST_DAY_VERSION, DATED_ALLOCATION_VERSIONS, allocate_sick_days, daily_benefit, maximum_contribution_base, sick_period_amount,
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



def _validate_joint_imported_sick_months(
    target_sources, other_histories, expected_contract_ids
):
    """Validate joint USC ceilings without merging separate job averages."""
    from calendar import monthrange

    if not expected_contract_ids:
        raise PayrollSickLeaveDerivationError(
            "Internal secondary requires a documented primary contract"
        )

    target = {}
    for item in target_sources:
        if item["source_type"] != "earnings_history":
            raise PayrollSickLeaveDerivationError(
                "Internal secondary requires fully imported attributed earnings"
            )
        month = item["source_period_start"]
        if month in target or month.day != 1:
            raise PayrollSickLeaveDerivationError(
                "Duplicate or invalid target insurance month"
            )
        amount = D(item["earnings_amount"])
        days = D(item["eligible_days"])
        if not amount.is_finite() or amount < 0 or not days.is_finite():
            raise PayrollSickLeaveDerivationError(
                "Invalid attributed target insurance earnings"
            )
        if days < 0 or days > monthrange(month.year, month.month)[1]:
            raise PayrollSickLeaveDerivationError(
                "Invalid target insurance days"
            )
        target[month] = amount

    if not target:
        raise PayrollSickLeaveDerivationError(
            "Internal secondary insurance history is empty"
        )

    ids = set(expected_contract_ids)
    found = {}
    for record in other_histories:
        contract_id = record.employment_contract_id
        month = record.month
        if contract_id not in ids or month not in target:
            raise PayrollSickLeaveDerivationError(
                "Unattributed or out-of-window insurance history"
            )
        key = (contract_id, month)
        if key in found:
            raise PayrollSickLeaveDerivationError(
                "Duplicate combined insurance history"
            )
        amount = D(record.sick_earnings)
        days = D(record.sick_days)
        if (
            not amount.is_finite() or amount < 0
            or not days.is_finite() or days < 0
            or days > monthrange(month.year, month.month)[1]
            or (amount > 0 and days == 0)
            or not record.source_reference
            or not record.source_reference.strip()
            or record.cutover_date <= month.replace(
                day=monthrange(month.year, month.month)[1]
            )
        ):
            raise PayrollSickLeaveDerivationError(
                "Unverified combined insurance source"
            )
        found[key] = amount

    expected = {
        (contract_id, month)
        for contract_id in ids
        for month in target
    }
    if set(found) != expected:
        raise PayrollSickLeaveDerivationError(
            "Incomplete combined insurance history"
        )

    result = []
    for month, own in sorted(target.items()):
        month_end = month.replace(
            day=monthrange(month.year, month.month)[1]
        )
        combined = own + sum(
            (found[(contract_id, month)] for contract_id in ids),
            D(0),
        )
        maximum = maximum_contribution_base(month_end)
        if combined > maximum:
            raise PayrollSickLeaveDerivationError(
                "Combined attributed insurance earnings exceed USC maximum"
            )
        result.append({
            "month": month.isoformat(),
            "combined_earnings": str(combined),
            "maximum": str(maximum),
            "contract_count": len(ids) + 1,
        })
    return result


async def _verified_internal_secondary_basis(
    db, *, company_id, contract, basis
):
    """Accept only complete internal histories within this ERP company."""
    from app.models.payroll_opening import PayrollEarningsHistory

    if contract.employment_kind != "internal_secondary":
        raise PayrollSickLeaveDerivationError(
            "External secondary requires verified other-employer evidence"
        )

    start = basis["reference_period_start"]
    end = basis["reference_period_end"]

    if start is None or end is None:
        raise PayrollSickLeaveDerivationError(
            "Secondary first-day replacement requires a reviewed rule"
        )

    colleagues = list((await db.scalars(
        select(EmploymentContract).where(
            EmploymentContract.company_id == company_id,
            EmploymentContract.employee_id == contract.employee_id,
            EmploymentContract.id != contract.id,
            EmploymentContract.status != "cancelled",
            EmploymentContract.start_date <= start,
            (
                EmploymentContract.end_date.is_(None)
                | (EmploymentContract.end_date >= end)
            ),
        )
    )).all())

    if (
        not colleagues
        or not any(c.employment_kind == "primary" for c in colleagues)
        or any(c.employment_kind == "external_secondary" for c in colleagues)
    ):
        raise PayrollSickLeaveDerivationError(
            "Complete same-company primary employment evidence required"
        )

    ids = [c.id for c in colleagues]
    histories = list((await db.scalars(
        select(PayrollEarningsHistory).where(
            PayrollEarningsHistory.company_id == company_id,
            PayrollEarningsHistory.employment_contract_id.in_(ids),
            PayrollEarningsHistory.month >= start,
            PayrollEarningsHistory.month <= end,
        )
    )).all())

    return _validate_joint_imported_sick_months(
        basis["sources"], histories, ids
    )


@serialized_payroll_mutation(PayrollSickLeaveConflictError)
async def calculate_sick_from_history(db, *, company_id, leave_request_id, insurance_evidence_id, calculated_by,
                                      _preview_replacement_of=None, _correction_excluded_ids=()):
    if _correction_excluded_ids and _preview_replacement_of is None:
        raise PayrollSickLeaveConflictError('Excluding old sources requires an atomic correction preview')
    saved = await get_sick_leave_calculation_for_leave(db,company_id=company_id,leave_request_id=leave_request_id)
    if saved and _preview_replacement_of is None:
        if saved.insurance_evidence_id != insurance_evidence_id or saved.rule_code != RULE_CODE:
            raise PayrollSickLeaveConflictError('Existing sick calculation has different evidence; use a correction')
        return saved
    evidence = await get_insurance_evidence(db,company_id=company_id,evidence_id=insurance_evidence_id,lock=True)
    if evidence.status != 'verified':
        raise PayrollInsuranceEvidenceError('Verified insurance evidence is required')
    if _preview_replacement_of is not None:
        if saved is None or saved.id != _preview_replacement_of:
            raise PayrollSickLeaveConflictError('Correction must identify the current sick calculation')
        previous_evidence = await get_insurance_evidence(
            db, company_id=company_id, evidence_id=saved.insurance_evidence_id, lock=True)
        if (previous_evidence.employee_id != evidence.employee_id
                or previous_evidence.case_start != evidence.case_start):
            raise PayrollSickLeaveConflictError('Correction evidence must identify the same employee and insurance case')

    leave = await db.scalar(select(LeaveRequest).where(LeaveRequest.company_id==company_id,LeaveRequest.id==leave_request_id))
    if leave is None or leave.status != 'approved' or leave.leave_type != 'sick':
        raise PayrollSickLeaveDerivationError('Approved sick leave is required')
    contract = await db.scalar(select(EmploymentContract).where(EmploymentContract.company_id==company_id,
        EmploymentContract.id==leave.employment_contract_id))
    if contract is None or contract.employee_id != evidence.employee_id:
        raise PayrollInsuranceEvidenceError('Insurance evidence belongs to another employee')
    if contract.employment_kind not in (
        'primary', 'internal_secondary'
    ):
        raise PayrollSickLeaveDerivationError(
            'External secondary employment requires verified other-employer insurance earnings'
        )
    if not date(2026,1,1) <= evidence.case_start <= leave.start_date <= leave.end_date <= date(2026,12,31):
        raise PayrollSickLeaveDerivationError('Insurance case requires a verified 2026 rule window')
    prior = (await db.execute(select(PayrollSickLeaveCalculation,LeaveRequest)
        .join(LeaveRequest,(LeaveRequest.company_id==PayrollSickLeaveCalculation.company_id)&
                          (LeaveRequest.id==PayrollSickLeaveCalculation.leave_request_id))
        .join(PayrollInsuranceEvidence,(PayrollInsuranceEvidence.company_id==PayrollSickLeaveCalculation.company_id)&
                                     (PayrollInsuranceEvidence.id==PayrollSickLeaveCalculation.insurance_evidence_id))
        .where(PayrollSickLeaveCalculation.company_id==company_id,
               PayrollSickLeaveCalculation.employment_contract_id==contract.id,
               current_sick_calculation_filter(),
               PayrollSickLeaveCalculation.id.not_in(_correction_excluded_ids),
               PayrollSickLeaveCalculation.id != _preview_replacement_of if _preview_replacement_of is not None else True,
               PayrollInsuranceEvidence.case_start==evidence.case_start)
        .order_by(LeaveRequest.start_date))).all()
    cursor = evidence.case_start
    for old, certificate in prior:
        if (certificate.start_date != cursor or certificate.end_date >= leave.start_date
            or certificate.status != 'approved' or old.insurance_evidence_id != evidence.id
            or old.rule_code != RULE_CODE or old.rule_version not in {*DATED_ALLOCATION_VERSIONS, LEGACY_RULE_VERSION}
            or old.rule_version != prior[0][0].rule_version):
            raise PayrollSickLeaveConflictError('Continuation requires contiguous approved certificates with the same evidence')
        cursor = certificate.end_date + timedelta(days=1)
    if cursor != leave.start_date:
        raise PayrollSickLeaveConflictError('Continuation has a gap or lacks the initial calculated certificate')
    limited = evidence.recent_service_months < 6
    if prior:
        first = prior[0][0]
        source_rows = await list_sick_leave_calculation_sources(db,company_id=company_id,sick_leave_calculation_id=first.id)
        basis = dict(reference_period_start=first.reference_period_start,reference_period_end=first.reference_period_end,
            eligible_earnings=first.eligible_earnings,eligible_days=first.eligible_days,
            sources=[dict(source_type=s.source_type,source_id=s.source_id,source_period_start=s.source_period_start,
                source_period_end=s.source_period_end,earnings_amount=s.earnings_amount,
                eligible_days=s.eligible_days,source_reference=s.source_reference) for s in source_rows
                if s.source_type != 'insurance_service_evidence'])
        basis['basis_method'] = (first.insurance_assessment or {}).get('basis_method', 'actual_history')
        basis['limit_reference_end'] = first.reference_period_end or date.fromisoformat(first.insurance_assessment['limit_reference_end'])
    elif evidence.case_start == contract.start_date:
        from app.services.payroll_sick_first_day_service import first_day_salary_basis
        basis = await first_day_salary_basis(db, company_id=company_id, contract=contract,
            case_start=evidence.case_start, limited=limited)
    else:
        basis = await derive_leave_average_history(db,company_id=company_id,leave_request_id=leave.id)
    secondary_joint_months = None
    if contract.employment_kind == 'internal_secondary':
        if prior:
            initial = prior[0][0]
            assessment = initial.insurance_assessment or {}
            if (
                assessment.get('combined_basis_scope')
                != 'same_company_imported_internal_secondary'
            ):
                raise PayrollSickLeaveDerivationError(
                    'Continuation lacks verified original joint insurance basis'
                )
            frozen = assessment.get('combined_insurance_months')
            if not isinstance(frozen, list) or not frozen:
                raise PayrollSickLeaveDerivationError(
                    'Continuation lacks saved joint insurance months'
                )
            seen_months = set()
            for item in frozen:
                if not isinstance(item, dict):
                    raise PayrollSickLeaveDerivationError(
                        'Invalid saved joint insurance month'
                    )
                try:
                    month = date.fromisoformat(item['month'])
                    combined = D(item['combined_earnings'])
                    maximum = D(item['maximum'])
                    contracts = item['contract_count']
                except (KeyError, ValueError, TypeError, ArithmeticError):
                    raise PayrollSickLeaveDerivationError(
                        'Invalid saved joint insurance month'
                    )
                if (
                    month.day != 1
                    or month in seen_months
                    or not combined.is_finite()
                    or not maximum.is_finite()
                    or combined < 0
                    or maximum <= 0
                    or combined > maximum
                    or not isinstance(contracts, int)
                    or isinstance(contracts, bool)
                    or contracts < 2
                ):
                    raise PayrollSickLeaveDerivationError(
                        'Invalid saved joint insurance month'
                    )
                seen_months.add(month)
            secondary_joint_months = frozen
        else:
            secondary_joint_months = await _verified_internal_secondary_basis(
                db, company_id=company_id, contract=contract, basis=basis
            )

    version = prior[0][0].rule_version if prior else (FIRST_DAY_VERSION if basis.get('basis_method') == 'first_day_monthly_salary' else RULE_VERSION)
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
    if version in DATED_ALLOCATION_VERSIONS:
        daily, maximum_reference_base = daily_benefit(earnings=basis['eligible_earnings'],
            days=basis['eligible_days'], percent=percent, reference_end=basis.get('limit_reference_end') or basis['reference_period_end'], limited=limited)
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
                   current_sick_calculation_filter(),
               PayrollSickLeaveCalculation.id.not_in(_correction_excluded_ids),
                   PayrollSickLeaveCalculation.id != _preview_replacement_of if _preview_replacement_of is not None else True,
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
        insurer_days=D(insurer_days),rule_code=RULE_CODE,rule_version=version,calculated_by=calculated_by,sources=sources, _preview=_preview_replacement_of is not None)
    if _preview_replacement_of is not None:
        row, preview_sources = row
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
        initial_calculation_id=prior[0][0].id if prior else (saved.id if _preview_replacement_of is not None else row.id),
        basis_method=basis.get('basis_method', 'actual_history'),
        limit_reference_end=(basis.get('limit_reference_end') or basis['reference_period_end']).isoformat(),
        sources=['https://zakon.rada.gov.ua/laws/show/1105-14',
                 'https://zakon.rada.gov.ua/laws/show/1266-2001-%D0%BF'])
    if secondary_joint_months is not None:
        row.insurance_assessment.update(
            combined_insurance_months=secondary_joint_months,
            combined_basis_scope='same_company_imported_internal_secondary',
        )
    if allocation is not None:
        row.insurance_assessment.update(daily_allocation=allocation, monthly_maximum=str(maximum_reference_base))
    if _preview_replacement_of is not None:
        return row, preview_sources
    await db.flush()
    return row
