from datetime import date
from decimal import Decimal as D
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from app.services.accounting_account_roles import AccountingAccountRole as R
from app.services.output_vat_gl_reconciliation_service import reconcile_output_vat_rows, reconcile_output_vat_gl, OutputVatGlReconciliationError
from app.api.v1 import output_vat_controls as api

DAY=date(2026,9,1)
IDS={R.TAX_SETTLEMENT:641, R.VAT_OUTPUT:643, R.GOODS_REVENUE:702}


def rows(*, amount='12', source='payment', reversed=False, journal=True, status='posted', entry_date=DAY):
    event=NS(id=1, tax_calculation_id=1, recognition_date=DAY, recognized_tax_amount=D(amount),
        reversal_of_id=9 if reversed else None, currency_code='UAH',
        invoice_fulfillment_allocation_id=1 if source=='supply' else None,
        payment_settlement_allocation_id=1 if source=='payment' else None,
        order_vat_advance_id=1 if source=='order' else None, tax_credit_evidence_id=None)
    calc=NS(id=1, trade_document_id=10, currency_code='UAH')
    entry=NS(id=2, reversal_of_id=8 if reversed else None, status=status, entry_date=entry_date)
    original=NS(id=8) if reversed else None
    if not journal:
        return [(event,calc,None,None,None)]
    debit=702 if source=='supply' else 643
    a,b=(641,debit) if reversed else (debit,641)
    return [(event,calc,entry,NS(id=3, account_id=a, debit=D(amount), credit=D(0)),original),
            (event,calc,entry,NS(id=4, account_id=b, debit=D(0), credit=D(amount)),original)]


def check(data, start=DAY, end=DAY):
    return reconcile_output_vat_rows(company_id=1,date_from=start,date_to=end,rows=data,account_ids=IDS)


@pytest.mark.parametrize('source',['payment','supply','order'])
@pytest.mark.parametrize('reversed',[False,True])
def test_exact_posting_and_reversal(source,reversed):
    result=check(rows(source=source,reversed=reversed))
    assert result.matched and result.difference==0
    assert result.posted_output_vat == D(-12 if reversed else 12)


def test_reversed_original_still_counts_in_historical_turnover():
    assert check(rows(status='reversed')).expected_output_vat == D(12)
    assert check(rows(status='reversed')).posted_output_vat == D(12)


@pytest.mark.parametrize('kwargs,code',[
    ({'journal':False},'missing_journal'),
    ({'status':'draft'},'journal_not_posted'),
    ({'entry_date':date(2026,10,1)},'journal_date_mismatch'),
    ({'amount':'0'},'unexpected_zero_tax_journal'),
    ({'source':'manual'},'invalid_event_source'),
    ({'amount':'NaN'},'invalid_event_amount'),
])
def test_source_anomalies_are_visible(kwargs,code):
    result=check(rows(**kwargs))
    assert not result.matched
    assert code in [i.code for i in result.issues]


def test_zero_tax_needs_no_journal():
    assert check(rows(amount='0',journal=False)).matched


def test_balanced_wrong_account_does_not_pass():
    data=rows(); data[0][3].account_id=999
    result=check(data)
    assert result.difference == 0 and not result.matched
    assert result.issues[0].code == 'journal_accounts_or_amounts_mismatch'


def test_equal_and_opposite_errors_do_not_cancel():
    first=rows(); second=rows()
    for item in second:
        item[0].id=2; item[2].id=4
    first[0][3].debit=D(13); first[1][3].credit=D(13)
    second[0][3].debit=D(11); second[1][3].credit=D(11)
    result=check(first+second)
    assert result.difference == 0 and not result.matched
    assert {i.event_id for i in result.issues} == {1,2}


def test_reversal_must_reference_original_journal():
    data=rows(reversed=True); data[0][2].reversal_of_id=999
    assert 'reversal_link_mismatch' in [i.code for i in check(data).issues]


@pytest.mark.asyncio
async def test_invalid_range_does_not_query_database():
    db=AsyncMock()
    with pytest.raises(OutputVatGlReconciliationError):
        await reconcile_output_vat_gl(db,company_id=1,date_from=date(2026,9,2),date_to=DAY)
    db.execute.assert_not_awaited()


def test_api_requires_authentication():
    app=FastAPI(); app.include_router(api.router)
    assert TestClient(app).get('/companies/1/vat-controls/output-gl?date_from=2026-09-01&date_to=2026-09-30').status_code==401


def test_api_denies_other_company_permission():
    from app.api.deps import get_current_user
    from app.core.database import get_db
    app=FastAPI(); app.include_router(api.router)
    db=AsyncMock(); db.execute.return_value=Mock(scalar_one_or_none=lambda:None)
    app.dependency_overrides[get_current_user]=lambda:NS(id=1)
    app.dependency_overrides[get_db]=lambda:db
    assert TestClient(app).get('/companies/2/vat-controls/output-gl?date_from=2026-09-01&date_to=2026-09-30').status_code==403


@pytest.mark.asyncio
async def test_endpoint_is_read_only(monkeypatch):
    result=check(rows())
    fn=AsyncMock(return_value=result); monkeypatch.setattr(api,'reconcile_output_vat_gl',fn)
    db=AsyncMock()
    assert await api.output_gl(1,DAY,DAY,db,None)==result
    db.commit.assert_not_awaited(); db.flush.assert_not_awaited()
    assert fn.await_args.kwargs==dict(company_id=1,date_from=DAY,date_to=DAY)


@pytest.mark.parametrize('amount',['NaN','Infinity','-Infinity'])
def test_nonfinite_amounts_cannot_build_output_journal(amount):
    from app.services.tax_recognition_accounting_service import (
        create_output_vat_recognition_accounting_plan, OutputVatRecognitionSourceKind,
        TaxRecognitionAccountingAmountError,
    )
    with pytest.raises(TaxRecognitionAccountingAmountError,match='finite'):
        create_output_vat_recognition_accounting_plan(source_kind=OutputVatRecognitionSourceKind.SETTLEMENT,amount=D(amount))


def test_output_cannot_use_input_evidence_alongside_payment():
    from app.services.tax_recognition_journal_service import resolve_output_vat_recognition_source_kind, TaxRecognitionJournalSourceStateError
    event=rows()[0][0]; event.tax_credit_evidence_id=9
    with pytest.raises(TaxRecognitionJournalSourceStateError,match='INPUT'):
        resolve_output_vat_recognition_source_kind(event)


@pytest.mark.asyncio
@pytest.mark.parametrize('amount',['NaN','Infinity','-Infinity'])
@pytest.mark.parametrize('reversed',[False,True])
async def test_nonfinite_output_event_cannot_post_or_reverse(amount,reversed):
    from app.services.tax_recognition_journal_service import (
        generate_and_post_output_vat_recognition_journal_entry, reverse_output_vat_recognition_journal_entry,
        TaxRecognitionJournalSourceStateError,
    )
    event=rows(amount=amount,reversed=reversed)[0][0]; event.company_id=1
    db=AsyncMock()
    with pytest.raises(TaxRecognitionJournalSourceStateError,match='nonfinite'):
        if reversed:
            await reverse_output_vat_recognition_journal_entry(db,reversal_event=event,reversed_by=1)
        else:
            await generate_and_post_output_vat_recognition_journal_entry(db,event=event,created_by=1)
    db.execute.assert_not_awaited()
