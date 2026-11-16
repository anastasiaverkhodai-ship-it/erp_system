from datetime import date
from decimal import Decimal
import pytest
from app.services.payroll_paid_income_calculation import allocate_remaining_net, paid_income_month, PaidIncomeError


def allocate(cash, previous=(), **kwargs):
    return allocate_remaining_net(gross='1000', pit='180', levy='50', cash=cash, previous=previous, **kwargs)


def test_net_input_partial_then_final_conserves_every_component():
    first = allocate('300')
    last = allocate('470', [first])
    assert first['net'] == '300.00'
    assert last['net'] == '470.00'
    for k, total in [('gross',1000),('pit',180),('levy',50),('net',770)]:
        assert Decimal(first[k])+Decimal(last[k]) == total


def test_advance_is_consumed_once_and_deduction_is_not_tax():
    advance = dict(gross='500.00',pit='90.00',levy='25.00',net='385.00')
    final = allocate('300', [advance], non_tax_withheld='85')
    assert final == advance
    with pytest.raises(PaidIncomeError): allocate('1',[advance,final])


def test_refund_of_advance_overwithholding_is_not_lost():
    result = allocate_remaining_net(gross='1000',pit='0',levy='50',
        previous=[dict(gross='500',pit='90',levy='25',net='385')],cash='565')
    assert result == dict(gross='500.00',pit='-90.00',levy='25.00',net='565.00')


@pytest.mark.parametrize('cash',[True,1.2,'NaN','0','-1','771'])
def test_invalid_or_excess_payment_rejected(cash):
    with pytest.raises(PaidIncomeError):allocate(cash)


def test_full_payment_has_no_rounding_remainder():
    assert allocate('770') == dict(gross='1000.00',pit='180.00',levy='50.00',net='770.00')


@pytest.mark.parametrize('day,expected',[(1,'2026-12'),(7,'2026-12'),(8,'2027-01')])
def test_salary_payment_deadline_and_year_boundary(day,expected):
    assert paid_income_month(accrual_month='2026-12',payment_date=date(2027,1,day),
        salary=True,contractual_due_date=date(2027,1,7)) == expected


def test_non_salary_never_uses_salary_exception():
    assert paid_income_month(accrual_month='2026-12',payment_date=date(2027,1,1),salary=False) == '2027-01'


def test_missing_schedule_is_not_assumed_timely():
    with pytest.raises(PaidIncomeError,match='deadline'):
        paid_income_month(accrual_month='2026-12',payment_date=date(2027,1,1),salary=True)
