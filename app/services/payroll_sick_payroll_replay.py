"""Revalue one sick source while retaining frozen payroll earnings and norms.

No writes or settlement authorization. The application service must persist the
returned revision and its complete financial effects in a single transaction.
"""
from decimal import Decimal
from app.services.payroll_correction_basis import verify_basis, replay_basis, _decode
from app.services.payroll_correction_source_evidence_service import _canonical_value, _fingerprint
from app.services.payroll_sick_allocation import sick_period_amount


class SickPayrollReplayError(ValueError):
    pass


def replay_sick_payroll(*, calculation, lines, original_sick, replacement_sick, leave, period):
    for source in (original_sick, replacement_sick):
        if (source.company_id != calculation.company_id
                or source.employment_contract_id != calculation.employment_contract_id
                or source.leave_request_id != leave.id
                or source.currency_code != calculation.currency_code):
            raise SickPayrollReplayError('Sick correction source identity mismatch')
    if (replacement_sick.supersedes_id != original_sick.id
            or replacement_sick.revision != original_sick.revision + 1
            or period.id != calculation.payroll_period_id
            or period.company_id != calculation.company_id):
        raise SickPayrollReplayError('Sick correction lineage or payroll period mismatch')
    basis = verify_basis(calculation, lines)
    payload = basis['payload']
    frozen = _decode(payload['lines'])
    matched = [line for line in frozen
               if line.get('source_sick_leave_calculation_id') == original_sick.id]
    if len(matched) != 1 or matched[0]['line_type'] != 'sick_pay':
        raise SickPayrollReplayError('Exactly one original sick-pay line is required')
    line = matched[0]
    old_days, old_amount = sick_period_amount(original_sick, leave, period.start_date, period.end_date)
    days, amount = sick_period_amount(replacement_sick, leave, period.start_date, period.end_date)
    if days <= 0 or days != old_days or line['quantity'] != old_days or line['amount'] != old_amount:
        raise SickPayrollReplayError('Original sick payroll does not reconcile to dated source')
    line.update(source_sick_leave_calculation_id=replacement_sick.id,
                quantity=Decimal(days), amount=amount, rate=replacement_sick.daily_benefit_amount)
    earnings = payload['facts']['earnings']
    frozen_sick_total = sum((Decimal(item['amount']) for item in earnings
                             if item['category'] == 'sick_pay'), Decimal(0))
    actual_sick_total = sum((Decimal(item['amount']) for item in frozen
                            if item['line_type'] == 'sick_pay'), Decimal(0))
    if frozen_sick_total + amount - old_amount != actual_sick_total:
        raise SickPayrollReplayError('Frozen sick-pay classifications do not reconcile')
    payload['facts']['earnings'] = [item for item in earnings if item['category'] != 'sick_pay']
    payload['facts']['earnings'].append(dict(category='sick_pay', amount=str(actual_sick_total)))
    payload['lines'] = _canonical_value(frozen)
    parent_fingerprint = basis['sha256']
    basis.pop('seal', None)
    basis['sha256'] = _fingerprint(payload)
    basis['parent_source_fingerprint'] = parent_fingerprint
    gross, derived, assessment = replay_basis(basis, calculation.minimum_wage_assessment.get('request'))
    assessment['correction_basis'] = basis
    return gross, derived, assessment
