"""Independent minimum-wage examples; premium pay must not absorb the guarantee."""
from decimal import Decimal as D
import pytest
from app.services.payroll_minimum_wage_service import (
    MinimumWageEarning as E, MinimumWageError, assess_minimum_wage,
)


def assess(minutes=10560, earnings=(), **kwargs):
    return assess_minimum_wage(rate_type=kwargs.get('rate_type','monthly'),
        minimum_rate=kwargs.get('minimum_rate',D('8647')),
        ordinary_worked_minutes=minutes,
        full_time_norm_minutes=kwargs.get('norm',10560),earnings=earnings)


def test_premiums_absences_and_assistance_do_not_absorb_guarantee():
    result=assess(earnings=[E('ordinary_salary',D('6000')),E('production_bonus',D('1000')),
        E('night',D('400')),E('overtime',D('900')),E('holiday_bonus',D('500')),
        E('vacation_pay',D('1000')),E('sick_pay',D('200')),E('hardship',D('3000'))])
    assert result.guaranteed_amount==D('8647')
    assert result.included_earnings==D('7000')
    assert result.excluded_earnings==D('6000')
    assert result.top_up==D('1647')


def test_part_time_uses_full_time_norm_and_half_guarantee():
    result=assess(minutes=5280,earnings=[E('ordinary_salary',D('3000'))])
    assert result.guaranteed_amount==D('4323.50')
    assert result.top_up==D('1323.50')


def test_no_work_produces_no_top_up_and_high_salary_is_not_reduced():
    assert assess(minutes=0,earnings=[E('vacation_pay',D('10000'))]).top_up==0
    assert assess(earnings=[E('ordinary_salary',D('10000'))]).top_up==0


def test_hourly_guarantee_and_minute_rounding():
    result=assess(minutes=61,rate_type='hourly',minimum_rate=D('52'),norm=None,
                  earnings=[E('ordinary_salary',D('40'))])
    assert result.guaranteed_amount==D('52.87')
    assert result.top_up==D('12.87')


@pytest.mark.parametrize('category',['manual_adjustment','unknown','rest_day'])
def test_unclassified_income_cannot_hide_a_shortfall(category):
    with pytest.raises(MinimumWageError,match='Unclassified'):
        assess(earnings=[E(category,D('10000'))])


@pytest.mark.parametrize('value',['NaN','Infinity','-1'])
def test_invalid_amounts_rejected(value):
    with pytest.raises(MinimumWageError):
        assess(earnings=[E('ordinary_salary',D(value))])


def test_invalid_time_and_precision_rejected():
    for kwargs in [dict(minutes=-1),dict(minutes=True),dict(minutes=10561),dict(norm=0)]:
        with pytest.raises(MinimumWageError):
            assess(**kwargs)
    with pytest.raises(MinimumWageError,match='cent precision'):
        assess(earnings=[E('ordinary_salary',D('1.001'))])
