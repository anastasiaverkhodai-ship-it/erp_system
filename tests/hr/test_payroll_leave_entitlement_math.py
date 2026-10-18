from datetime import date, timedelta
from decimal import Decimal as D
import pytest
from app.services.payroll_leave_entitlement_math import (
    EntitlementSegment as Segment, annual_entitlement, rounded_entitlement_buckets,
    compensation_amount,
)


def test_leap_year_and_part_time_leave_duration():
    start,end=date(2024,1,1),date(2024,12,31)
    result=annual_entitlement(year_start=start,year_end=end,through=end,
        segments=[Segment(start,end,D(24))])
    assert result['total']==24 and result['year_days']==366
    assert rounded_entitlement_buckets(result)==dict(before_2024=D(0),from_2024=D(24),total=D(24))


def test_disability_rate_change_and_service_exclusions():
    start,end=date(2026,1,1),date(2026,12,31)
    excluded={date(2026,1,1)+timedelta(days=i) for i in range(31)}
    result=annual_entitlement(year_start=start,year_end=end,through=end,
        segments=[Segment(start,date(2026,6,30),D(24)),Segment(date(2026,7,1),end,D(30))],
        excluded_dates=excluded)
    assert result['total']==(D(150)*24+D(184)*30)/365
    assert result['year_days']==365 and result['eligible_days']==334


def test_2023_split_does_not_round_each_bucket_independently():
    start,end=date(2023,7,1),date(2024,6,30)
    result=annual_entitlement(year_start=start,year_end=end,through=end,
        segments=[Segment(start,end,D(24))])
    rounded=rounded_entitlement_buckets(result)
    assert rounded['before_2024']+rounded['from_2024']==rounded['total']==24
    assert result['before_2024']==D(184)*24/366


def test_unknown_and_overlapping_rates_rejected():
    start,end=date(2026,1,1),date(2026,12,31)
    with pytest.raises(ValueError,match='Missing'):
        annual_entitlement(year_start=start,year_end=end,through=end,segments=[])
    with pytest.raises(ValueError,match='Overlapping'):
        annual_entitlement(year_start=start,year_end=end,through=end,
            segments=[Segment(start,end,D(24)),Segment(start,end,D(30))])


def test_compensation_uses_unrounded_quotient():
    assert compensation_amount(days=D(24),eligible_earnings=D('10000'),eligible_days=D(31))==D('7741.94')
    with pytest.raises(ValueError):
        compensation_amount(days=D(1),eligible_earnings=D('NaN'),eligible_days=D(31))


def test_disability_annual_guarantees_require_evidence():
    from pydantic import ValidationError
    from app.schemas.payroll_leave_balance import LeaveAllowanceSegment
    from datetime import date
    import pytest
    for group, minimum in [('I',30),('II',30),('III',26)]:
        data=dict(start=date(2026,1,1),end=date(2026,12,31),disability_group=group,
            eligibility_reference='Dated disability confirmation')
        with pytest.raises(ValidationError,match='guarantee'):
            LeaveAllowanceSegment(**data,annual_days=24)
        assert LeaveAllowanceSegment(**data,annual_days=minimum).annual_days==minimum
        with pytest.raises(ValidationError,match='supporting evidence'):
            LeaveAllowanceSegment(**dict(data,eligibility_reference=None),annual_days=minimum)
