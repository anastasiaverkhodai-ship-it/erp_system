"""Versioned computational evidence for minimum-wage-context corrections only.

Freeze the earnings and classification inputs actually consumed at calculation
creation. Replay never queries today's supplements, leave or employment terms.
Legacy calculations without the database creation seal remain ineligible.
This does not authorize tax changes, accounting or payment execution.
"""
from copy import deepcopy
from datetime import date
from decimal import Decimal

from app.services.payroll_correction_source_evidence_service import _canonical_value, _fingerprint
from app.services.payroll_minimum_wage_service import (
    MinimumWageEarning, MinimumWageError, assess_minimum_wage, normalize_minimum_wage_context,
)

SCHEMA = 'payroll-minimum-context-basis-v1'
SEAL = 'creation-028-v1'


def _decode(value):
    if isinstance(value, list):
        return [_decode(x) for x in value]
    if isinstance(value, dict):
        if set(value) == {'decimal'}:
            return Decimal(value['decimal'])
        if set(value) == {'type', 'value'} and value['type'] == 'date':
            return date.fromisoformat(value['value'])
        return {k: _decode(v) for k, v in value.items()}
    return value


def make_basis(*, company_id, period, payroll_input, salary_slices, facts, lines):
    payload = dict(schema=SCHEMA, company_id=company_id, payroll_period_id=period.id,
                   payroll_input_id=payroll_input.id,
                   employment_contract_id=payroll_input.employment_contract_id,
                   facts=deepcopy(facts), lines=_canonical_value(lines))
    return dict(payload=payload, sha256=_fingerprint(payload))


def replay_basis(basis, context):
    """Replay only the documented norm/classification, keeping other earnings fixed."""
    payload = basis['payload']
    facts = payload['facts']
    if payload.get('schema') != SCHEMA or facts.get('rule_version') != 'UA-4695-IX-2026-v1':
        raise MinimumWageError('Unsupported correction basis version')
    context = normalize_minimum_wage_context(context)
    norm = context.get('full_time_norm_minutes', facts['default_norm_minutes'])
    if facts['rate_type'] == 'monthly' and norm is None:
        raise MinimumWageError('A documented full-time norm is required')
    earnings = [MinimumWageEarning(e['category'], Decimal(e['amount'])) for e in facts['earnings']]
    if facts['has_adjustment']:
        category = context.get('manual_adjustment_category')
        if category is None:
            raise MinimumWageError('Manual adjustment requires an explicit minimum-wage classification')
        totals = {}
        for earning in earnings:
            totals[earning.category] = totals.get(earning.category, Decimal(0)) + earning.amount
        totals[category] = totals.get(category, Decimal(0)) + Decimal(facts['adjustment'])
        earnings = [MinimumWageEarning(k, v) for k, v in totals.items()]
    elif context.get('manual_adjustment_category'):
        raise MinimumWageError('Adjustment classification has no matching earning line')
    result = assess_minimum_wage(rate_type=facts['rate_type'], minimum_rate=facts['minimum_rate'],
        ordinary_worked_minutes=facts['ordinary_worked_minutes'], full_time_norm_minutes=norm,
        earnings=earnings)
    assessment = dict(status='assessed', rule_version=facts['rule_version'],
        source_reference='https://zakon.rada.gov.ua/laws/show/4695-IX',
        rate_type=facts['rate_type'], minimum_rate=facts['minimum_rate'],
        ordinary_worked_minutes=facts['ordinary_worked_minutes'], full_time_norm_minutes=norm,
        guaranteed_amount=str(result.guaranteed_amount), included_earnings=str(result.included_earnings),
        excluded_earnings=str(result.excluded_earnings), top_up=str(result.top_up), request=context)
    lines = _decode(deepcopy(payload['lines']))
    if result.top_up:
        lines.append(dict(line_no=len(lines)+1, line_type='minimum_wage',
            description='Minimum-wage top-up; UA-4695-IX-2026-v1', quantity=Decimal(1),
            rate=result.top_up, amount=result.top_up, currency_code='UAH',
            source_effective_from=date.fromisoformat(facts['period_start']),
            source_effective_to=date.fromisoformat(facts['period_end'])))
    return sum((Decimal(line['amount']) for line in lines), Decimal('0.00')), lines, assessment


def verify_basis(calculation, lines):
    assessment = calculation.minimum_wage_assessment or {}
    basis = assessment.get('correction_basis')
    if not isinstance(basis, dict) or basis.get('seal') != SEAL:
        raise MinimumWageError('Payroll source evidence is incomplete; creation-time basis required')
    payload = basis.get('payload')
    if not isinstance(payload, dict) or basis.get('sha256') != _fingerprint(payload):
        raise MinimumWageError('Correction basis fingerprint mismatch')
    for name in ('company_id', 'payroll_period_id', 'payroll_input_id', 'employment_contract_id'):
        if payload.get(name) != getattr(calculation, name):
            raise MinimumWageError('Correction basis identity mismatch')
    gross, expected_lines, expected_assessment = replay_basis(basis, assessment.get('request'))
    actual_assessment = {k: v for k, v in assessment.items() if k != 'correction_basis'}
    if gross != calculation.gross_amount or actual_assessment != expected_assessment:
        raise MinimumWageError('Original assessment does not reconcile to its creation-time basis')
    if len(lines) != len(expected_lines):
        raise MinimumWageError('Original calculation membership differs from its basis')
    for actual, expected in zip(lines, expected_lines):
        for key, value in expected.items():
            actual_value = getattr(actual, key)
            actual_value = getattr(actual_value, 'value', actual_value)
            if actual_value != value:
                raise MinimumWageError('Original calculation line differs from its basis')
        # No source identifier may be attached after the basis was built.
        for key in ('source_salary_rate_id', 'source_supplement_id',
                    'source_vacation_calculation_id', 'source_sick_leave_calculation_id', 'source_leave_compensation_line_id'):
            if getattr(actual, key, None) != expected.get(key):
                raise MinimumWageError('Calculation source attribution differs from its basis')
    return deepcopy(basis)
