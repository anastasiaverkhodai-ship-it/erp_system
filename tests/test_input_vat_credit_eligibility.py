from datetime import date
from decimal import Decimal
import pytest
from pydantic import ValidationError
from app.schemas.input_vat_credit_claim import InputVatCreditClaimCreate
from app.services.input_vat_credit_eligibility_service import assess_input_vat_credit

TODAY=date(2026,9,16)


def payload(**updates):
    values=dict(request_key='PN-1',tax_calculation_id=1,invoice_number='PN-1',invoice_date='2026-08-10',
        registration_status='registered',registered_on='2026-09-05',receipt_reference='receipt-001',
        buyer_vat_number='111',supplier_vat_number='222',claim_period='2026-08-01',taxable_base='60',tax_amount='12')
    values.update(updates)
    return InputVatCreditClaimCreate(**values)


def assess(**updates):
    return assess_input_vat_credit(payload(**updates),as_of_date=TODAY)


def test_timely_registration_allows_invoice_month():
    result=assess()
    assert result.eligible and result.timely_registration
    assert result.registration_deadline==date(2026,9,5)
    assert result.credit_available_date==date(2026,8,10)


def test_late_registration_requires_registration_month():
    result=assess(registered_on='2026-09-06')
    assert not result.eligible and result.reason=='claim_period_before_eligibility'
    result=assess(registered_on='2026-09-06',claim_period='2026-09-01')
    assert result.eligible and result.credit_available_date==date(2026,9,6)


@pytest.mark.parametrize('day,deadline',[(15,5),(16,18),(31,18)])
def test_two_registration_windows(day,deadline):
    result=assess(invoice_date=date(2026,8,day),claim_period='2026-09-01')
    assert result.registration_deadline==date(2026,9,deadline)


@pytest.mark.parametrize('status',['pending','suspended','rejected'])
def test_nonregistered_document_never_qualifies(status):
    result=assess(registration_status=status,registered_on=None,receipt_reference=None)
    assert not result.eligible and result.reason=='registration_not_confirmed'


@pytest.mark.parametrize('values',[
    {'receipt_reference':None}, {'registered_on':None}, {'tax_amount':'NaN'},
    {'tax_amount':'Infinity'}, {'claim_period':'2026-09-02'}, {'tax_amount':'0'},
    {'request_key':'   '}, {'credit_available_date':'2026-01-01'},
])
def test_invalid_or_caller_controlled_dates_rejected(values):
    with pytest.raises(ValidationError):
        payload(**values)


def test_expiry_is_last_eligible_month_not_declaration_submission_day():
    result=assess(invoice_date='2025-08-10',registered_on='2025-08-11',claim_period='2026-08-01')
    assert result.eligible and result.expires_on==date(2026,8,10)
    assert not assess(invoice_date='2025-08-10',registered_on='2025-08-11',claim_period='2026-09-01').eligible


def test_documented_suspension_extends_deadline():
    result=assess(invoice_date='2025-08-10',registered_on='2026-09-01',claim_period='2026-09-01',
        suspensions=[dict(suspended_on='2025-08-11',resumed_on='2025-09-11',decision_reference='suspension decision')])
    assert result.eligible and result.expires_on==date(2026,9,10)


@pytest.mark.parametrize('intervals',[
    [('2025-08-11','2025-09-11'),('2025-09-01','2025-10-01')],
    [('2026-08-11','2026-09-01')],
    [('2025-08-01','2025-08-12')],
    [('2026-09-01','2026-09-05')],
])
def test_invalid_suspension_intervals_do_not_extend_right(intervals):
    result=assess(invoice_date='2025-08-10',registered_on='2026-09-01',claim_period='2026-09-01',
        suspensions=[dict(suspended_on=a,resumed_on=b,decision_reference='decision') for a,b in intervals])
    assert not result.eligible


@pytest.mark.parametrize('values',[
    dict(invoice_date='2023-07-31'), dict(invoice_date='2026-09-17'),
    dict(registered_on='2026-09-17'), dict(registered_on='2026-08-01'),
    dict(claim_period='2026-10-01'),
])
def test_unsupported_history_and_future_dates_fail_closed(values):
    assert not assess(**values).eligible
