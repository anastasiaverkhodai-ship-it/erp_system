from copy import deepcopy
from datetime import date
from types import SimpleNamespace
import pytest
from pydantic import ValidationError
from app.schemas.payroll_leave_balance import LeaveAccountCreate
from app.services.payroll_leave_balance_service import social_entitled_days


def basis():
    return dict(
        employment_contract_id=1,leave_kind='social',entitlement_code='parental',
        period_start='2026-01-01',period_end='2026-12-31',social_days=10,
        social_grounds=['two_children_under_15'],source_reference='HR entitlement review',
        social_year_review_reference='Prior use and termination compensation checked',request_key='parental',
        social_evidence=[dict(ground='two_children_under_15',valid_from='2026-01-01',valid_to='2026-12-31',
            dependants=[dict(document_reference='child-1',birth_date='2015-03-01'),
                        dict(document_reference='child-2',birth_date='2018-08-01')],
            eligibility_reference='Verified birth certificates',
            other_parent_nonuse_reference='Other parent yearly confirmation')])


def amount(data, through):
    validated=LeaveAccountCreate(**data)
    account=SimpleNamespace(basis=validated.model_dump(mode='json'),period_start=date(2026,1,1))
    return social_entitled_days(account,SimpleNamespace(start_date=date(2025,1,1),end_date=None),through)


def test_documented_social_right_is_annual_not_prorated():
    data=basis()
    assert amount(data,date(2026,1,1))==10
    assert amount(data,date(2026,12,31))==10


@pytest.mark.parametrize('change', ['unknown','missing','duplicate','no_other_parent','old_children','mismatched'])
def test_invalid_social_right_cannot_inflate_allowance(change):
    data=basis()
    evidence=data['social_evidence'][0]
    if change=='unknown':
        data['social_grounds']=['arbitrary-second-ground']
    elif change=='missing':
        data['social_evidence']=[]
    elif change=='duplicate':
        data['social_evidence']*=2;data['social_grounds']*=2;data['social_days']=17
    elif change=='no_other_parent':
        evidence.pop('other_parent_nonuse_reference')
    elif change=='old_children':
        evidence['dependants'][0]['birth_date']='2008-03-01'
    else:
        evidence['ground']='single_parent'
    with pytest.raises(ValidationError):
        LeaveAccountCreate(**data)


def test_duplicate_child_is_not_a_second_child():
    data=basis()
    data['social_evidence'][0]['dependants']*=2
    with pytest.raises(ValidationError,match='counted twice'):
        LeaveAccountCreate(**data)


def test_age_limit_and_second_birth_must_overlap():
    data=basis()
    evidence=data['social_evidence'][0]
    evidence['dependants'][0]['birth_date']='2011-03-01'
    evidence['dependants'][1]['birth_date']='2026-08-01'
    with pytest.raises(ValidationError,match='simultaneously'):
        LeaveAccountCreate(**data)
    evidence['dependants'][1]['birth_date']='2026-02-01'
    assert amount(data,date(2026,1,31))==0
    assert amount(data,date(2026,2,1))==10
    assert amount(data,date(2026,12,31))==10


def test_second_distinct_ground_adds_seven_days_only_after_it_arises():
    data=basis()
    second=deepcopy(data['social_evidence'][0])
    second.update(ground='single_parent',valid_from='2026-07-01',
                  eligibility_reference='Documented sole-parent status from July')
    data.update(social_days=17,social_grounds=['two_children_under_15','single_parent'],
                social_evidence=[data['social_evidence'][0],second])
    assert amount(data,date(2026,6,30))==10
    assert amount(data,date(2026,7,1))==17


def test_full_civil_capacity_limits_future_years_without_erasing_acquired_right():
    data=basis()
    evidence=data['social_evidence'][0]
    evidence['ground']='single_parent'
    evidence['dependants']=[dict(document_reference='child',birth_date='2009-01-02',full_capacity_date='2026-05-01')]
    data['social_grounds']=['single_parent']
    assert amount(data,date(2026,12,31))==10
    evidence['dependants'][0]['full_capacity_date']='2025-05-01'
    with pytest.raises(ValidationError,match='eligible child'):
        LeaveAccountCreate(**data)


@pytest.mark.parametrize('ground', [
    'child_disability','adopted_child','adult_disability_A_I',
    'single_parent','guardian','foster_parent',
])
def test_each_statutory_ground_requires_and_accepts_its_documented_dependant(ground):
    data=basis()
    evidence=data['social_evidence'][0]
    evidence['ground']=ground
    data['social_grounds']=[ground]
    evidence['dependants']=evidence['dependants'][:1]
    if ground=='adult_disability_A_I':
        evidence['dependants'][0].update(birth_date='2000-02-01',adult_disability_A_I=True)
    assert amount(data,date(2026,12,31))==10
    if ground=='adult_disability_A_I':
        evidence['dependants'][0]['adult_disability_A_I']=False
        with pytest.raises(ValidationError,match='category A group I'):
            LeaveAccountCreate(**data)
