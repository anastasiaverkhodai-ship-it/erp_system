from datetime import date
from decimal import Decimal as D
from types import SimpleNamespace
import pytest
from app.services.payroll_sick_allocation import (
    ALLOCATION_VERSION, daily_benefit, allocate_sick_days, sick_period_amount,
)


@pytest.mark.parametrize('reference,daily,monthly', [
    (date(2025,12,31), D('5256.24'), D(160000)),
    (date(2026,1,31), D('5681.34'), D(172940)),
])
def test_daily_and_monthly_maximum(reference, daily, monthly):
    actual, maximum = daily_benefit(earnings=D(1000000),days=100,percent=100,
        reference_end=reference,limited=False)
    assert (actual, maximum) == (daily, monthly)
    parts = allocate_sick_days(start=date(2026,3,1),end=date(2026,3,31),
        case_start=date(2026,3,1),daily=actual,maximum=maximum)
    assert sum(D(p['amount']) for p in parts) == monthly
    assert sum(D(p['amount']) for p in parts if p['payer']=='employer') == daily*5
    assert D(parts[-1]['amount']) == monthly-daily*30


def test_percentage_applies_after_average_ceiling_and_short_service_after_percentage():
    kwargs=dict(earnings=D(1000000),days=100,percent=50,reference_end=date(2025,12,31))
    assert daily_benefit(**kwargs,limited=False)[0] == D('2628.12')
    assert daily_benefit(**kwargs,limited=True)[0] == D('284.07')


def test_split_certificates_consume_one_monthly_limit_and_keep_first_five_days():
    kwargs=dict(case_start=date(2026,1,1),daily=D('5256.24'),maximum=D(160000))
    first=allocate_sick_days(start=date(2026,1,1),end=date(2026,1,20),**kwargs)
    next_=allocate_sick_days(start=date(2026,1,21),end=date(2026,2,3),
        previous={'2026-01':sum(D(x['amount']) for x in first)},**kwargs)
    whole=allocate_sick_days(start=date(2026,1,1),end=date(2026,2,3),**kwargs)
    assert first+next_ == whole
    assert all(x['payer']=='insurer' for x in next_)
    assert sum(D(x['amount']) for x in next_ if x['date'].startswith('2026-02')) == D('15768.72')


def test_saved_daily_allocation_is_used_for_partial_payroll_and_validated():
    entries=allocate_sick_days(start=date(2026,1,1),end=date(2026,2,3),
        case_start=date(2026,1,1),daily=D('5256.24'),maximum=D(160000))
    row=SimpleNamespace(rule_version=ALLOCATION_VERSION,insurance_assessment={'daily_allocation':entries},
        sick_pay_amount=D('175768.72'),employer_amount=D('26281.20'),insurer_amount=D('149487.52'))
    leave=SimpleNamespace(start_date=date(2026,1,1),end_date=date(2026,2,3))
    assert sick_period_amount(row,leave,date(2026,1,1),date(2026,1,31)) == (31,D(160000))
    assert sick_period_amount(row,leave,date(2026,2,1),date(2026,2,28)) == (3,D('15768.72'))
    assert sick_period_amount(row,leave,date(2026,1,31),date(2026,1,31)) == (1,D('2312.80'))
    entries[-1]['amount']='1'
    with pytest.raises(ValueError,match='reconcile'):
        sick_period_amount(row,leave,date(2026,1,1),date(2026,2,28))
