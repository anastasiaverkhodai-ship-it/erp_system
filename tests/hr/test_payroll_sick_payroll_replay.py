from copy import deepcopy
from datetime import date
from decimal import Decimal as D
from types import SimpleNamespace as NS
import pytest
from test_payroll_correction_basis import original
from app.services.payroll_correction_basis import replay_basis, SEAL, verify_basis
from app.services.payroll_correction_source_evidence_service import _canonical_value, _fingerprint
from app.services.payroll_sick_payroll_replay import replay_sick_payroll, SickPayrollReplayError


def scenario(new_amount='1500'):
    calc, lines = original()
    calc.currency_code = 'UAH'
    basis = calc.minimum_wage_assessment['correction_basis']
    payload = basis['payload']
    payload['facts']['earnings'].append(dict(category='sick_pay', amount='1000'))
    payload['lines'].extend(_canonical_value([dict(line_no=2, line_type='sick_pay',
        quantity=D(5), rate=D(200), amount=D(1000), currency_code='UAH',
        source_sick_leave_calculation_id=10)]))
    basis['sha256'] = _fingerprint(payload)
    gross, lines, assessment = replay_basis(basis, calc.minimum_wage_assessment['request'])
    calc.gross_amount = gross
    calc.minimum_wage_assessment = dict(assessment, correction_basis=basis)
    old = NS(id=10, company_id=1, employment_contract_id=4, leave_request_id=20,
        currency_code='UAH', revision=1, supersedes_id=None, insurance_assessment={},
        rule_version='legacy', sick_pay_amount=D(1000), daily_benefit_amount=D(200))
    new = deepcopy(old)
    new.id, new.revision, new.supersedes_id = 11, 2, 10
    new.sick_pay_amount, new.daily_benefit_amount = D(new_amount), D(new_amount)/5
    return dict(calculation=calc, lines=[NS(**line) for line in lines], original_sick=old,
        replacement_sick=new, leave=NS(id=20, start_date=date(2026,9,1), end_date=date(2026,9,5)),
        period=NS(id=2, company_id=1, start_date=date(2026,9,1), end_date=date(2026,9,30)))


@pytest.mark.parametrize('amount', ['1500', '500', '0'])
def test_replay_changes_only_sick_earnings_and_retains_guarantee(amount):
    args = scenario(amount)
    calc = args['calculation']
    before = deepcopy(calc.minimum_wage_assessment)
    gross, lines, assessment = replay_sick_payroll(**args)
    assert gross == calc.gross_amount + D(amount) - 1000
    assert lines[0]['amount'] == D(3000)
    assert lines[1]['source_sick_leave_calculation_id'] == 11
    assert lines[1]['amount'] == D(amount)
    assert assessment['top_up'] == before['top_up']
    assert calc.minimum_wage_assessment == before
    assert 'seal' not in assessment['correction_basis']
    # A future database creation seal must attest a self-consistent new basis.
    assessment['correction_basis']['seal'] = SEAL
    replacement = deepcopy(calc)
    replacement.gross_amount, replacement.minimum_wage_assessment = gross, assessment
    verify_basis(replacement, [NS(**line) for line in lines])


@pytest.mark.parametrize('field,value', [('company_id',9), ('employment_contract_id',8),
    ('leave_request_id',7), ('supersedes_id',99), ('revision',5), ('currency_code','EUR')])
def test_replay_rejects_wrong_source_identity(field, value):
    args = scenario()
    setattr(args['replacement_sick'], field, value)
    with pytest.raises(SickPayrollReplayError):
        replay_sick_payroll(**args)


def test_replay_rejects_unreconciled_original():
    args = scenario()
    args['original_sick'].sick_pay_amount = D(999)
    with pytest.raises(SickPayrollReplayError, match='reconcile'):
        replay_sick_payroll(**args)
