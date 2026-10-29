"""Replay a sealed payroll using independently derived leave source values.

The application supplies verified replacements; this pure helper cannot approve
leave, persist a correction, authorize recovery, or create a payment.
"""
from decimal import Decimal
from app.services.payroll_correction_basis import verify_basis, replay_basis, _decode
from app.services.payroll_correction_source_evidence_service import _canonical_value, _fingerprint


class LeavePayrollReplayError(ValueError):
    pass


SOURCE_FIELDS = {'vacation_pay': 'source_vacation_calculation_id',
                 'leave_compensation': 'source_leave_compensation_line_id'}


def replay_leave_payroll(*, calculation, lines, replacements):
    """Replace exactly identified leave lines, retaining every other frozen input.

    Each replacement is {line_type, source_id, quantity, amount, rate}.
    Zero quantity/amount removes the earning while retaining source evidence in
    the owning immutable application. Negative earnings are never manufactured.
    """
    if not replacements:
        raise LeavePayrollReplayError('Verified leave replacements are required')
    basis = verify_basis(calculation, lines)
    payload = basis['payload']
    frozen = _decode(payload['lines'])
    seen = set()
    changed_categories = set()
    for replacement in replacements:
        kind = replacement.get('line_type')
        if kind not in SOURCE_FIELDS:
            raise LeavePayrollReplayError('Only leave earnings may be replaced')
        source_id = replacement.get('source_id')
        if type(source_id) is not int or source_id <= 0:
            raise LeavePayrollReplayError('A positive source identity is required')
        key = (kind, replacement.get("source_kind","original"), source_id)
        if key in seen:
            raise LeavePayrollReplayError('Duplicate leave replacement')
        seen.add(key)
        source_kind=replacement.get('source_kind','original')
        if source_kind not in {'original','extra'} or (source_kind=='extra' and kind!='leave_compensation'):
            raise LeavePayrollReplayError('Invalid leave source kind')
        field = 'source_leave_compensation_extra_id' if source_kind=='extra' else SOURCE_FIELDS[kind]
        matched = [line for line in frozen if line.get(field) == source_id]
        if not matched and kind=='leave_compensation' and replacement.get('allow_new_compensation') is True:
            # The source-owning service verifies membership in the original
            # compensation. A zero-day source may have had no prior earning line.
            matched=[dict(line_type=kind,amount=Decimal(0),quantity=Decimal(0),
                rate=Decimal(0),currency_code=calculation.currency_code,**{field:source_id})]
            frozen.append(matched[0])
        if len(matched) != 1 or matched[0]['line_type'] != kind:
            raise LeavePayrollReplayError('Exactly one matching frozen leave source is required')
        values = {}
        for name in ('quantity', 'amount', 'rate'):
            value = replacement.get(name)
            if isinstance(value, bool) or not isinstance(value, Decimal) or not value.is_finite() or value < 0:
                raise LeavePayrollReplayError('Leave replacement values must be finite nonnegative decimals')
            values[name] = value
        if values['amount'] != values['amount'].quantize(Decimal('.01')):
            raise LeavePayrollReplayError('Leave amount must be expressed in cents')
        if values['quantity'] == 0 and values['amount'] != 0:
            raise LeavePayrollReplayError('Zero leave days cannot carry an earning')
        old_total = sum((line['amount'] for line in frozen if line['line_type'] == kind), Decimal(0))
        if kind not in changed_categories:
            classified = sum((Decimal(item['amount']) for item in payload['facts']['earnings']
                              if item['category'] == kind), Decimal(0))
            if classified != old_total:
                raise LeavePayrollReplayError('Original leave classification does not reconcile')
        changed_categories.add(kind)
        if values['quantity'] == 0:
            frozen.remove(matched[0])
        else:
            matched[0].update(values)
    for number, line in enumerate(frozen, 1):
        line['line_no'] = number
    earnings = [item for item in payload['facts']['earnings'] if item['category'] not in changed_categories]
    for kind in sorted(changed_categories):
        total = sum((line['amount'] for line in frozen if line['line_type'] == kind), Decimal(0))
        if total:
            earnings.append({'category': kind, 'amount': str(total)})
    payload['facts']['earnings'] = earnings
    payload['lines'] = _canonical_value(frozen)
    parent = basis['sha256']
    basis.pop('seal', None)
    basis['sha256'] = _fingerprint(payload)
    basis['parent_source_fingerprint'] = parent
    gross, derived, assessment = replay_basis(basis, calculation.minimum_wage_assessment.get('request'))
    assessment['correction_basis'] = basis
    return gross, derived, assessment
