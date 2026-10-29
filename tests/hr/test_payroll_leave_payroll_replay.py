from copy import deepcopy
from decimal import Decimal as D
from types import SimpleNamespace as NS
import pytest
from test_payroll_correction_basis import original
from app.services.payroll_correction_basis import replay_basis, SEAL, verify_basis
from app.services.payroll_correction_source_evidence_service import _canonical_value, _fingerprint
from app.services.payroll_leave_payroll_replay import replay_leave_payroll, LeavePayrollReplayError


def scenario(kind='vacation_pay',amount='500',quantity='2'):
    calc,_=original();calc.currency_code='UAH'
    basis=calc.minimum_wage_assessment['correction_basis'];payload=basis['payload']
    payload['facts']['earnings'].append(dict(category=kind,amount='1000'))
    field='source_vacation_calculation_id' if kind=='vacation_pay' else 'source_leave_compensation_line_id'
    payload['lines'].extend(_canonical_value([dict(line_no=2,line_type=kind,quantity=D(4),
        rate=D(250),amount=D(1000),currency_code='UAH',**{field:10})]))
    basis['sha256']=_fingerprint(payload)
    gross,lines,assessment=replay_basis(basis,calc.minimum_wage_assessment['request'])
    calc.gross_amount=gross;calc.minimum_wage_assessment=dict(assessment,correction_basis=basis)
    return dict(calculation=calc,lines=[NS(**line) for line in lines],replacements=[dict(
        line_type=kind,source_id=10,quantity=D(quantity),rate=D(250),amount=D(amount))])


@pytest.mark.parametrize('kind',['vacation_pay','leave_compensation'])
@pytest.mark.parametrize('amount,quantity',[('500','2'),('1500','6'),('0','0')])
def test_replay_preserves_other_earnings_and_sealed_source(kind,amount,quantity):
    args=scenario(kind,amount,quantity);calc=args['calculation'];before=deepcopy(calc.minimum_wage_assessment)
    gross,lines,assessment=replay_leave_payroll(**args)
    assert gross==calc.gross_amount-1000+D(amount)
    assert lines[0]['amount']==3000
    assert assessment['top_up']==before['top_up']
    assert calc.minimum_wage_assessment==before
    assert 'seal' not in assessment['correction_basis']
    assessment['correction_basis']['seal']=SEAL
    replacement=deepcopy(calc);replacement.gross_amount=gross;replacement.minimum_wage_assessment=assessment
    verify_basis(replacement,[NS(**line) for line in lines])


@pytest.mark.parametrize('field,value', [('amount',D('NaN')),('amount',D('-1')),('amount',D('1.001')),
    ('quantity',D('-1')),('source_id',999),('line_type','salary'),('rate',True)])
def test_rejects_invalid_replacement(field,value):
    args=scenario();args['replacements'][0][field]=value
    with pytest.raises(LeavePayrollReplayError):replay_leave_payroll(**args)


def test_rejects_duplicate_source():
    args=scenario();args['replacements']*=2
    with pytest.raises(LeavePayrollReplayError,match='Duplicate'):replay_leave_payroll(**args)


def test_restores_zero_compensation_source_only_with_verified_membership():
    args=scenario('leave_compensation','0','0')
    gross,lines,assessment=replay_leave_payroll(**args)
    assessment['correction_basis']['seal']=SEAL
    calc=deepcopy(args['calculation']);calc.gross_amount=gross;calc.minimum_wage_assessment=assessment
    replacement=dict(line_type='leave_compensation',source_id=10,quantity=D(2),amount=D(500),rate=D(250))
    with pytest.raises(LeavePayrollReplayError,match='Exactly one'):
        replay_leave_payroll(calculation=calc,lines=[NS(**line) for line in lines],replacements=[replacement])
    new_gross,new_lines,new_assessment=replay_leave_payroll(calculation=calc,
        lines=[NS(**line) for line in lines],replacements=[dict(replacement,allow_new_compensation=True)])
    assert new_gross==gross+500
    assert next(line for line in new_lines if line['line_type']=='leave_compensation')['source_leave_compensation_line_id']==10
    new_assessment['correction_basis']['seal']=SEAL
    calc.gross_amount=new_gross;calc.minimum_wage_assessment=new_assessment
    verify_basis(calc,[NS(**line) for line in new_lines])
