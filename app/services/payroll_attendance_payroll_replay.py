"""Replay ordinary salary from reviewed daily time and immutable salary terms."""
from copy import deepcopy
from decimal import Decimal, ROUND_HALF_UP
from app.services.payroll_correction_basis import verify_basis, replay_basis, _decode
from app.services.payroll_correction_source_evidence_service import _canonical_value, _fingerprint


class AttendancePayrollReplayError(ValueError):
    pass


def replay_attendance_payroll(*, calculation, lines, slices, monthly_norm_minutes, changes):
    """Changes carry independently authenticated old/new minutes for each date.

    The service must verify attendance ownership, absence occupancy and review.
    This helper neither writes records nor authorizes a payment.
    """
    basis = verify_basis(calculation, lines)
    payload = basis['payload']
    frozen = _decode(deepcopy(payload['lines']))
    if not changes or len({x['work_date'] for x in changes}) != len(changes):
        raise AttendancePayrollReplayError('Distinct reviewed attendance dates required')
    salary = [x for x in frozen if x['line_type'] == 'salary']
    delta = 0
    for item in changes:
        for key in ('old_minutes', 'new_minutes'):
            if type(item[key]) is not int or not 0 <= item[key] <= 1440:
                raise AttendancePayrollReplayError('Attendance minutes must be between zero and 1440')
        delta += item['new_minutes'] - item['old_minutes']
    classified = sum((Decimal(x['amount']) for x in payload['facts']['earnings']
                      if x['category'] == 'ordinary_salary'), Decimal(0))
    old_salary = sum((x['amount'] for x in salary), Decimal(0))
    covered = set()
    overtime_base_delta = Decimal(0)
    overtime_minutes_delta = 0
    for source in slices:
        matched = [x for x in salary if x.get('source_salary_rate_id') == source.salary_rate_id
                   and x.get('source_effective_from') == source.effective_from
                   and x.get('source_effective_to') == source.effective_to]
        if len(matched) != 1:
            raise AttendancePayrollReplayError('Exactly one frozen salary slice required')
        selected = [x for x in changes if source.effective_from <= x['work_date'] <= source.effective_to]
        covered.update(x['work_date'] for x in selected)
        if not selected:
            continue
        previous = payload.get('attendance_slice_minutes', {}).get(str(source.id), source.worked_minutes)
        minutes = previous + sum(x['new_minutes']-x['old_minutes'] for x in selected)
        replacements=[item['premium_replacement'] for item in selected if 'premium_replacement' in item]
        extra_delta=sum(sum(p['minutes'] for p in r['new'] if p['kind']!='night')-
            sum(p['minutes'] for p in r['old'] if p['kind']!='night') for r in replacements)
        overtime_delta=sum(sum(p['minutes'] for p in r['new'] if p['kind']=='overtime')-
            sum(p['minutes'] for p in r['old'] if p['kind']=='overtime') for r in replacements)
        overtime_minutes_delta+=overtime_delta
        old_extra=selected[0].get('old_slice_extra',0)
        if not 0 <= minutes <= source.scheduled_minutes+old_extra+extra_delta:
            raise AttendancePayrollReplayError('Ordinary work must fit the confirmed schedule; classify extra time separately')
        line = matched[0]
        rate = Decimal(source.salary_rate_amount)
        kind = getattr(source.salary_rate_type, 'value', source.salary_rate_type)
        if kind == 'monthly':
            if not monthly_norm_minutes or monthly_norm_minutes <= 0:
                raise AttendancePayrollReplayError('Verified monthly norm required')
            amount = rate * minutes / monthly_norm_minutes
        elif kind == 'hourly':
            amount = rate * minutes / 60
            line['quantity'] = (Decimal(minutes)/60).quantize(Decimal('.000001'), rounding=ROUND_HALF_UP)
        else:
            raise AttendancePayrollReplayError('Unsupported salary rate type')
        line['amount'] = amount.quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
        denominator=monthly_norm_minutes if kind=='monthly' else 60
        old_overtime=selected[0].get('old_slice_overtime',0)
        old_base=(rate*old_overtime/denominator).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
        new_base=(rate*(old_overtime+overtime_delta)/denominator).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
        overtime_base_delta+=new_base-old_base
        payload.setdefault('attendance_slice_minutes', {})[str(source.id)] = minutes
    if covered != {x['work_date'] for x in changes}:
        raise AttendancePayrollReplayError('Attendance date outside verified salary slices')
    worked = payload['facts']['ordinary_worked_minutes'] + delta - overtime_minutes_delta
    if worked < 0:
        raise AttendancePayrollReplayError('Negative corrected ordinary work')
    payload['facts']['ordinary_worked_minutes'] = worked
    for item in changes:
        payload.setdefault('attendance_day_minutes', {})[item['work_date'].isoformat()] = item['new_minutes']
    new_classified = classified + sum((x['amount'] for x in salary), Decimal(0)) - old_salary - overtime_base_delta
    category_delta={'overtime':overtime_base_delta}
    categories={'night':'night','overtime':'overtime','rest_day':'ordinary_salary','regular_extra':'ordinary_salary'}
    for change in changes:
        replacement=change.get('premium_replacement')
        if replacement is None:continue
        old_ids={p['source_id'] for p in replacement['old']}
        frozen=[line for line in frozen if line.get('source_supplement_id') not in old_ids]
        for side,sign in (('old',-1),('new',1)):
            for premium in replacement[side]:
                category=categories[premium['kind']]
                category_delta[category]=category_delta.get(category,Decimal(0))+sign*Decimal(premium['amount'])
        for premium in replacement['new']:
            frozen.append(dict(line_no=0,line_type='supplement',description='Reviewed '+premium['kind'],
                quantity=(Decimal(premium['minutes'])/60).quantize(Decimal('.0001'),rounding=ROUND_HALF_UP),
                rate=Decimal(premium['rate']),amount=Decimal(premium['amount']),currency_code='UAH',
                source_supplement_id=premium['source_id'],source_effective_from=change['work_date'],
                source_effective_to=change['work_date']))
        payload.setdefault('attendance_day_overtime',{})[change['work_date'].isoformat()]=sum(
            p['minutes'] for p in replacement['new'] if p['kind']=='overtime')
    new_classified+=category_delta.pop('ordinary_salary',Decimal(0))
    for category,change_amount in category_delta.items():
        total=sum((Decimal(x['amount']) for x in payload['facts']['earnings'] if x['category']==category),Decimal(0))+change_amount
        if total<0:raise AttendancePayrollReplayError('Corrected premium classification cannot be negative')
        payload['facts']['earnings']=[x for x in payload['facts']['earnings'] if x['category']!=category]
        payload['facts']['earnings'].append(dict(category=category,amount=str(total)))
    for index,line in enumerate(frozen,1):line['line_no']=index
    if new_classified < 0:
        raise AttendancePayrollReplayError('Corrected salary classification cannot be negative')
    payload['facts']['earnings'] = [x for x in payload['facts']['earnings'] if x['category'] != 'ordinary_salary']
    payload['facts']['earnings'].append(dict(category='ordinary_salary', amount=str(new_classified)))
    payload['lines'] = _canonical_value(frozen)
    basis.pop('seal', None)
    basis['sha256'] = _fingerprint(payload)
    gross, derived, assessment = replay_basis(basis, calculation.minimum_wage_assessment.get('request'))
    assessment['correction_basis'] = basis
    return gross, derived, assessment
