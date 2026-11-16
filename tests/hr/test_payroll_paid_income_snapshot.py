import copy
from hashlib import sha256
from types import SimpleNamespace
import pytest
from app.services.payroll_paid_income_calculation import PaidIncomeError
from app.services.payroll_paid_income_service import allocation_from_record, canonical, report_month_for_record


def body():
    return dict(schema='PAYROLL_PAID_INCOME_V1',company_id=1,payment_id=2,journal_id=3,
        allocation=dict(gross='100.00',pit='18.00',levy='5.00',net='77.00'),
        monthly_source=dict(gross='1000.00',pit='180.00',levy='50.00'),
        previous_allocations=[],cash='77.00',non_tax_withheld='0.00',
        policy='PROSPECTIVE_REMAINING_NET_SHARE_V1',tax_remittance_inferred=False,
        payment_date='2026-10-07',accrual_month='2026-09',income_line_types=['salary'],
        salary_deadline={'date':'2026-10-07'})


def record(value):
    return SimpleNamespace(company_id=1,payroll_disbursement_id=2,journal_entry_id=3,
        snapshot_json=canonical(dict(body=value,sha256=sha256(canonical(value).encode()).hexdigest())))


def test_rehashed_but_invented_allocation_is_rejected():
    value=body()
    assert allocation_from_record(record(value))==value
    value['allocation'].update(gross='101.00',pit='19.00')
    with pytest.raises(PaidIncomeError,match='Corrupt'):
        allocation_from_record(record(value))


@pytest.mark.parametrize('value',[None,[],{},'invalid'])
def test_invalid_envelopes_raise_domain_error(value):
    row=record(body());row.snapshot_json=canonical(value)
    with pytest.raises(PaidIncomeError,match='Corrupt'):
        allocation_from_record(row)


@pytest.mark.parametrize('day,expected',[('2026-10-07','2026-09'),('2026-10-08','2026-10'),('2026-11-01','2026-11')])
def test_frozen_salary_deadline(day,expected):
    value=body();value['payment_date']=day
    assert report_month_for_record(value)==expected


def test_unknown_mixed_income_is_not_silently_salary():
    value=body();value['income_line_types']=['salary','severance']
    with pytest.raises(PaidIncomeError,match='classification'):
        report_month_for_record(value)


def test_late_mixed_payment_uses_actual_month_without_guessing_classification():
    value=body();value['income_line_types']=['salary','severance']
    value['payment_date']='2026-11-01'
    assert report_month_for_record(value)=='2026-11'
