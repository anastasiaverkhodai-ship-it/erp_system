from datetime import date
from decimal import Decimal as D
from types import SimpleNamespace as NS
import pytest
from app.services.ar_gl_control_service import SPECS as AR_SPECS
from app.services.purchase_gl_control_service import SIMPLE_SPECS
SPECS = AR_SPECS + SIMPLE_SPECS
from app.services.event_gl_control_service import compare_event_rows

DAY = date(2026, 9, 1)


def sample(spec, reversed=False):
    event = NS(id=1, currency_code='UAH', reversal_of_id=9 if reversed else None)
    setattr(event, spec.date_field, DAY)
    setattr(event, spec.amount_field, D(12))
    entry = NS(id=2, status='posted', entry_date=DAY, reversal_of_id=8 if reversed else None)
    original = NS(id=8) if reversed else None
    a, b = (200,100) if reversed else (100,200)
    return [(event,entry,NS(id=3,account_id=a,debit=D(12),credit=D(0)),original),
            (event,entry,NS(id=4,account_id=b,debit=D(0),credit=D(12)),original)]


def check(spec, rows):
    return compare_event_rows(spec=spec,rows=rows,
        account_ids={spec.debit_role:100,spec.credit_role:200},focus_account_id=100,
        normal_credit=False,date_from=DAY,date_to=DAY)


@pytest.mark.parametrize('spec',SPECS)
@pytest.mark.parametrize('reversed',[False,True])
def test_source_and_reversal(spec,reversed):
    result=check(spec,sample(spec,reversed))
    assert result.matched
    assert result.expected_amount==result.posted_amount==D(-12 if reversed else 12)


@pytest.mark.parametrize('spec',SPECS)
def test_missing_and_wrong_journals(spec):
    rows=sample(spec)
    assert not check(spec,[(rows[0][0],None,None,None)]).matched
    rows[0][2].account_id=999
    result=check(spec,rows)
    assert not result.matched
    assert 'journal_accounts_or_amounts_mismatch' in [i.code for i in result.issues]


@pytest.mark.parametrize('spec',SPECS)
def test_offsetting_errors_remain_visible(spec):
    a,b=sample(spec),sample(spec)
    b[0][0].id=2
    for _,_,line,_ in a:
        if line.debit:line.debit+=1
        if line.credit:line.credit+=1
    for _,_,line,_ in b:
        if line.debit:line.debit-=1
        if line.credit:line.credit-=1
    result=check(spec,a+b)
    assert result.difference==0 and not result.matched
    assert {i.source_id for i in result.issues}=={1,2}
