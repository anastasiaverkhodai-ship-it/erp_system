from copy import deepcopy
from decimal import Decimal
from types import SimpleNamespace

import pytest
from app.services.payroll_correction_basis import verify_basis, replay_basis, SCHEMA, SEAL
from app.services.payroll_correction_source_evidence_service import _fingerprint, _canonical_value
from app.services.payroll_minimum_wage_service import MinimumWageError


def original():
    payload = dict(schema=SCHEMA, company_id=1, payroll_period_id=2, payroll_input_id=3,
        employment_contract_id=4, facts=dict(rate_type='monthly', minimum_rate='8647',
            ordinary_worked_minutes=5280, default_norm_minutes=None,
            earnings=[dict(category='ordinary_salary', amount='3000.00')],
            adjustment='0', has_adjustment=False, period_start='2026-09-01',
            period_end='2026-09-30', rule_version='UA-4695-IX-2026-v1'),
        lines=_canonical_value([dict(line_no=1, line_type='salary', quantity=Decimal(1),
            rate=Decimal('3000'), amount=Decimal('3000'), currency_code='UAH')]))
    basis = dict(payload=payload, sha256=_fingerprint(payload), seal=SEAL)
    gross, lines, assessment = replay_basis(basis, dict(full_time_norm_minutes=10560, norm_reference='Calendar'))
    calc = SimpleNamespace(company_id=1, payroll_period_id=2, payroll_input_id=3,
        employment_contract_id=4, gross_amount=gross, minimum_wage_assessment=dict(assessment, correction_basis=basis))
    return calc, [SimpleNamespace(**row) for row in lines]


@pytest.mark.parametrize('change', ['fingerprint', 'identity', 'amount', 'membership', 'seal', 'attribution'])
def test_tampering_cannot_authorize_correction(change):
    calc, lines = original()
    assert verify_basis(calc, lines)['seal'] == SEAL
    basis = calc.minimum_wage_assessment['correction_basis']
    if change == 'fingerprint': basis['payload']['facts']['minimum_rate'] = '99999'
    if change == 'identity': calc.company_id = 5
    if change == 'amount': lines[0].amount = Decimal('1')
    if change == 'membership': lines.append(deepcopy(lines[0]))
    if change == 'seal': basis.pop('seal')
    if change == 'attribution': lines[0].source_supplement_id = 6
    with pytest.raises(MinimumWageError):
        verify_basis(calc, lines)


def test_cannot_remove_required_documented_norm():
    calc, _ = original()
    with pytest.raises(MinimumWageError, match='documented'):
        replay_basis(calc.minimum_wage_assessment['correction_basis'], {})
