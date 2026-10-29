from datetime import date
from decimal import Decimal as D
from types import SimpleNamespace as NS
import pytest
from test_payroll_correction_basis import original
from app.services.payroll_correction_basis import replay_basis, verify_basis, SEAL
from app.services.payroll_correction_source_evidence_service import _fingerprint
from app.services.payroll_attendance_payroll_replay import replay_attendance_payroll, AttendancePayrollReplayError


def scenario():
    calc,_=original()
    basis=calc.minimum_wage_assessment['correction_basis']
    basis['payload']['lines'][0].update(source_salary_rate_id=1,
        source_effective_from={'type':'date','value':'2026-09-01'},
        source_effective_to={'type':'date','value':'2026-09-30'})
    basis['sha256']=_fingerprint(basis['payload'])
    gross,lines,assessment=replay_basis(basis,calc.minimum_wage_assessment['request'])
    calc.gross_amount=gross;calc.minimum_wage_assessment=dict(assessment,correction_basis=basis)
    part=NS(id=1,salary_rate_id=1,effective_from=date(2026,9,1),effective_to=date(2026,9,30),
        salary_rate_type='monthly',salary_rate_amount=D(6000),worked_minutes=5280,scheduled_minutes=10560)
    return dict(calculation=calc,lines=[NS(**line) for line in lines],slices=[part],monthly_norm_minutes=10560,
        changes=[dict(work_date=date(2026,9,10),old_minutes=0,new_minutes=480)])


def test_reviewed_work_replays_salary_and_guarantee_without_mutating_old_basis():
    args=scenario();old=args['calculation'].gross_amount
    gross,lines,assessment=replay_attendance_payroll(**args)
    assert lines[0]['amount']==D('3272.73')
    assert assessment['ordinary_worked_minutes']==5760
    assert gross==D('4716.55')
    assert args['calculation'].gross_amount==old
    assessment['correction_basis']['seal']=SEAL
    calc=args['calculation'];calc.gross_amount=gross;calc.minimum_wage_assessment=assessment
    verify_basis(calc,[NS(**line) for line in lines])
    # A second correction uses the reviewed minutes, not the original slice.
    args.update(calculation=calc,lines=[NS(**line) for line in lines],
        changes=[dict(work_date=date(2026,9,10),old_minutes=480,new_minutes=0)])
    final,_,_=replay_attendance_payroll(**args)
    assert final==old


@pytest.mark.parametrize('value',[-1,1441,True])
def test_reject_invalid_minutes(value):
    args=scenario();args['changes'][0]['new_minutes']=value
    with pytest.raises(AttendancePayrollReplayError):replay_attendance_payroll(**args)
