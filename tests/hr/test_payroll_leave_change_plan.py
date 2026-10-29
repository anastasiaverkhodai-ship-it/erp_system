from datetime import date as day
from decimal import Decimal as D
import pytest
from app.services.payroll_leave_change_plan import LeaveRange, LeaveChangeError, plan_leave_change


def request(**overrides):
    data=dict(original=LeaveRange(day(2026,9,28),day(2026,10,4)),
        used=(LeaveRange(day(2026,9,28),day(2026,9,30)),),transferred=(),
        original_amount=D('100.00'),leave_kind='annual',operation='recall',
        effective_date=day(2026,10,1),order_reference='Order 123',
        consent_reference='Signed consent 456',recall_ground='industrial_accident')
    data.update(overrides)
    return plan_leave_change(**data)


def test_recall_preserves_daily_rounding_and_releases_exact_days():
    plan=request()
    assert plan.retained_amount==D('42.86')
    assert plan.reduction_amount==D('57.14')
    assert len(plan.released_dates)==4
    assert plan.monthly_amounts==((day(2026,9,1),D('42.86')),)


def test_transfer_does_not_copy_old_average_to_new_leave():
    plan=request(operation='transfer',transferred=(LeaveRange(day(2026,11,2),day(2026,11,5)),))
    assert len(plan.transferred_dates)==4
    assert plan.retained_amount+plan.reduction_amount==D('100.00')


@pytest.mark.parametrize('kind',['annual','additional','social'])
def test_partial_use_all_paid_kinds(kind):
    plan=request(operation='partial',leave_kind=kind)
    assert len(plan.used_dates)==3


@pytest.mark.parametrize('overrides,match',[
    ({'leave_kind':'social'},'restricted'),
    ({'consent_reference':''},'consent'),
    ({'recall_ground':'convenience'},'statutory'),
    ({'recall_ground':'other_statutory'},'legal source'),
    ({'original_amount':D('NaN')},'money'),
    ({'operation':'transfer'},'every'),
    ({'used':(LeaveRange(day(2026,9,28),day(2026,10,1)),)},'return to work'),
    ({'transferred':(LeaveRange(day(2026,11,1),day(2026,11,9)),)},'exceed'),
    ({'used':(LeaveRange(day(2026,9,28),day(2026,9,29)),LeaveRange(day(2026,9,29),day(2026,9,30)))},'overlap'),
])
def test_invalid_changes_are_rejected(overrides,match):
    with pytest.raises(LeaveChangeError,match=match):request(**overrides)


def test_noncontiguous_used_dates_preserve_each_original_cent():
    plan=request(operation='partial', used=(LeaveRange(day(2026,9,28),day(2026,9,28)),
        LeaveRange(day(2026,10,4),day(2026,10,4))))
    assert plan.retained_amount==D('28.58')
    assert len(plan.monthly_amounts)==2
