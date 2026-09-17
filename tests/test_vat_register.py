from copy import deepcopy
from datetime import date, datetime, timezone
from decimal import Decimal as D
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.services.vat_register_service import build_vat_register, VatRegisterError

START=date(2026,9,1)
END=date(2026,9,30)
NOW=datetime(2026,10,1,tzinfo=timezone.utc)
STAMP='2026-09-01T10:00:00+00:00'


def fixture(direction='output'):
    event=dict(id=1,company_id=1,tax_calculation_id=1,recognition_date='2026-09-01',recognized_taxable_base='100.00',recognized_tax_amount='20.00',currency_code='UAH',reversal_of_id=None,created_at=STAMP,tax_credit_evidence_id=1 if direction=='input' else None)
    header=dict(id=1,company_id=1,direction=direction,document_number='PN1',document_date='2026-09-01',seller_vat_number='123',created_at=STAMP)
    line=dict(id=1,company_id=1,tax_invoice_id=1,line_number=1,taxable_base='100',tax_amount='20',tax_rate_code='VAT20',tax_recognition_event_id=1,tax_calculation_id=1)
    j=dict(id=1,company_id=1,tax_recognition_event_id=1,entry_date='2026-09-01',created_at=STAMP,posted_at=STAMP,status='posted',reversal_of_id=None)
    debit,credit=(3,1) if direction=='output' else (1,2)
    return dict(companies=[dict(id=1,chart_of_accounts_template='general_291')],accounts=[dict(id=i,company_id=1,code=code) for i,code in enumerate(['641','644','643','702'],1)],
        tax_calculations=[dict(id=1,company_id=1,direction=direction,tax_type='vat')],tax_recognition_events=[event],tax_invoices=[header],tax_invoice_lines=[line],
        tax_invoice_registration_events=[dict(id=1,company_id=1,tax_invoice_id=1,event_date='2026-09-01',status='registered',created_at=STAMP)],
        tax_invoice_credit_evidence_links=[dict(id=1,company_id=1,tax_invoice_id=1,tax_calculation_id=1,tax_credit_evidence_id=1)] if direction=='input' else [],
        tax_credit_evidence=[dict(id=1,company_id=1,evidenced_taxable_base='100',evidenced_tax_amount='20')],
        journal_entries=[j],journal_entry_lines=[dict(id=1,journal_entry_id=1,account_id=debit,debit='20',credit='0'),dict(id=2,journal_entry_id=1,account_id=credit,debit='0',credit='20')])


def report(data,**kwargs):
    return build_vat_register(data,company_id=1,date_from=START,date_to=END,as_of=NOW,**kwargs)


@pytest.mark.parametrize('direction',['input','output'])
def test_full_document_event_gl_chain(direction):
    result=report(fixture(direction))
    assert result['matched'],result['issues']
    assert result['totals'][direction]['expected_vat']==D(20)
    assert result['totals'][direction]['posted_vat']==D(20)


@pytest.mark.parametrize('mutation,code',[
    (lambda d:d['tax_invoice_lines'][0].update(tax_amount='19'),'document_source_amount_mismatch'),
    (lambda d:d.update(tax_invoices=[],tax_invoice_lines=[]),'missing_tax_document'),
    (lambda d:d.update(journal_entries=[],journal_entry_lines=[]),'missing_journal'),
    (lambda d:d['journal_entries'][0].update(entry_date='2026-10-01'),'journal_date_mismatch'),
    (lambda d:d['journal_entries'][0].update(status='draft',posted_at=None),'journal_not_posted_at_cutoff'),
    (lambda d:d['journal_entry_lines'][0].update(account_id=4),'journal_accounts_or_amounts_mismatch'),
    (lambda d:d['tax_invoice_registration_events'][0].update(status='suspended'),'document_not_registered'),
])
def test_source_anomalies(mutation,code):
    data=fixture();mutation(data)
    result=report(data)
    assert not result['matched']
    assert code in [i['code'] for i in result['issues']]


def test_late_registration_is_not_visible_at_cutoff():
    data=fixture()
    data['tax_invoice_registration_events'][0]['created_at']='2026-10-02T10:00:00+00:00'
    result=report(data)
    assert result['register'][0]['registration_status']=='unknown'
    assert not result['matched']


def test_filters_do_not_hide_errors():
    data=fixture();data['journal_entries']=[]
    result=report(data,direction='input')
    assert result['register']==[] and not result['matched']


def test_unclassified_641_entry_is_explicit():
    data=fixture()
    data['journal_entries'][0]['tax_recognition_event_id']=None
    result=report(data)
    assert result['unclassified_tax_settlement']==D(20)
    assert 'unclassified_tax_settlement_journal' in [i['code'] for i in result['issues']]


def test_cross_company_fails_closed():
    data=fixture();data['tax_invoices'][0]['company_id']=2
    with pytest.raises(VatRegisterError,match='Cross-company'):report(data)


def test_input_document_amount_is_not_credit_claim_amount():
    data=fixture('input')
    data['tax_recognition_events'][0].update(recognized_taxable_base='50',recognized_tax_amount='10')
    for line in data['journal_entry_lines']:
        line['debit']=str(D(line['debit'])/2);line['credit']=str(D(line['credit'])/2)
    result=report(data)
    assert result['matched'],result['issues']
    assert result['register_totals']['input']['tax_amount']==D(20)
    assert result['totals']['input']['expected_vat']==D(10)


def test_declaration_compares_source_and_header_even_when_errors_offset():
    data=fixture()
    data['vat_declarations']=[dict(id=1,company_id=1,period_start=str(START),period_end=str(END),source_cutoff_at=NOW.isoformat(),created_at='2026-10-02T00:00:00+00:00',output_taxable_base='100',output_vat='20',input_taxable_base='0',input_vat_credit='0')]
    data['vat_declaration_source_lines']=[dict(id=1,company_id=1,vat_declaration_id=1,source_kind='output_tax_recognition',tax_recognition_event_id=1,taxable_base_delta='100',tax_amount_delta='20',direction='output',economic_effective_date='2026-09-01')]
    assert report(data,declaration_id=1)['matched']
    data['vat_declaration_source_lines'][0]['tax_amount_delta']='19'
    assert 'declaration_source_mismatch' in [i['code'] for i in report(data,declaration_id=1)['issues']]


def test_api_authentication():
    response=TestClient(app).get('/api/v1/companies/1/vat-registers',params=dict(date_from=str(START),date_to=str(END),as_of=NOW.isoformat()))
    assert response.status_code==401


@pytest.mark.parametrize('kind,table,base_field,tax_field,fk,legal_kind,adjustment_table,adjustment_fk,sign',[
    ('purchase_return_input_credit_correction','purchase_return_input_vat_credit_correction_events','reduced_taxable_base','reduced_tax_amount','purchase_return_input_vat_credit_correction_event_id','purchase_return','purchase_return_vat_adjustment_events','purchase_return_vat_adjustment_event_id',-1),
    ('purchase_value_input_credit_correction','purchase_value_correction_input_vat_credit_correction_events','corrected_taxable_base','corrected_tax_amount','purchase_value_correction_input_vat_credit_correction_event_id','purchase_value_correction','purchase_value_correction_vat_adjustment_events','purchase_value_correction_vat_adjustment_event_id',-1),
    ('purchase_value_input_credit_correction','purchase_value_correction_input_vat_credit_correction_events','corrected_taxable_base','corrected_tax_amount','purchase_value_correction_input_vat_credit_correction_event_id','purchase_value_correction','purchase_value_correction_vat_adjustment_events','purchase_value_correction_vat_adjustment_event_id',1),
])
def test_input_correction_chain(kind,table,base_field,tax_field,fk,legal_kind,adjustment_table,adjustment_fk,sign):
    data=fixture('input')
    data[table]=[dict(id=2,company_id=1,adjustment_date='2026-09-02',created_at=STAMP,currency_code='UAH',correction_kind='increase' if sign==1 else 'decrease',**{base_field:'50',tax_field:'10',adjustment_fk:2})]
    data[adjustment_table]=[dict(id=2,company_id=1,adjustment_date='2026-09-02',adjusted_taxable_base='50',adjusted_tax_amount='10',adjustment_kind='increase' if sign==1 else 'decrease')]
    data['tax_invoice_corrections']=[dict(id=2,company_id=1,direction='input',document_number='RK1',document_date='2026-09-02',seller_vat_number='123',created_at=STAMP,original_tax_invoice_id=1)]
    data['tax_invoice_correction_lines']=[dict(id=2,company_id=1,tax_invoice_correction_id=2,line_number=1,source_kind=legal_kind,taxable_base_delta=str(sign*50),tax_amount_delta=str(sign*10),tax_rate_code='VAT20',**{adjustment_fk:2})]
    data['tax_invoice_correction_registration_events']=[dict(id=2,company_id=1,tax_invoice_correction_id=2,status='registered',event_date='2026-09-02',created_at=STAMP)]
    data['journal_entries'].append(dict(id=2,company_id=1,entry_date='2026-09-02',status='posted',created_at=STAMP,posted_at=STAMP,**{fk:2}))
    debit,credit=(1,2) if sign==1 else (2,1)
    data['journal_entry_lines'] += [dict(id=3,journal_entry_id=2,account_id=debit,debit='10',credit='0'),dict(id=4,journal_entry_id=2,account_id=credit,debit='0',credit='10')]
    result=report(data)
    assert result['matched'],result['issues']
    assert result['totals']['input']['expected_vat']==D(20+sign*10)
    assert result['register_totals']['input']['tax_amount']==D(20+sign*10)


@pytest.mark.parametrize('kind,table,values',[
    ('sales_return','sales_return_recognition_events',dict(recognition_date='2026-09-02',returned_gross_amount='60',returned_tax_amount='10')),
    ('sales_value_correction','trade_value_correction_events',dict(direction='sale',correction_date='2026-09-02',original_gross_amount='120',original_tax_amount='20',corrected_gross_amount='60',corrected_tax_amount='10')),
])
def test_sales_adjustments_without_tax_posting_cannot_be_green(kind,table,values):
    data=fixture();data[table]=[dict(id=2,company_id=1,created_at=STAMP,currency_code='UAH',**values)]
    result=report(data)
    assert result['totals']['output']['expected_vat']==D(10)
    assert result['totals']['output']['posted_vat']==D(20)
    assert {'code':'missing_vat_posting_contract','source_kind':kind,'source_id':2} in result['issues']


def test_opposite_document_errors_do_not_cancel():
    data=fixture()
    data['tax_invoice_lines'][0]['tax_amount']='19'
    other=deepcopy(data['tax_invoice_lines'][0]);other.update(id=2,line_number=2,tax_amount='21')
    data['tax_invoice_lines'].append(other)
    result=report(data)
    assert result['totals']['output']['difference']==0
    assert not result['matched']
    assert 'duplicate_source_coverage' in [i['code'] for i in result['issues']]


def test_debit_and_credit_gross_corruption_does_not_cancel():
    data=fixture();data['journal_entry_lines'][0].update(debit='21',credit='1')
    result=report(data)
    assert result['totals']['output']['difference']==0
    assert 'journal_accounts_or_amounts_mismatch' in [i['code'] for i in result['issues']]


def test_naive_request_cutoff_rejected_but_legacy_utc_journals_supported():
    data=fixture();data['journal_entries'][0]['posted_at']='2026-09-01T10:00:00'
    assert report(data)['matched']
    with pytest.raises(VatRegisterError,match='timezone'):
        build_vat_register(data,company_id=1,date_from=START,date_to=END,as_of=NOW.replace(tzinfo=None))


def test_unrelated_old_document_error_does_not_break_period():
    data=fixture()
    old=deepcopy(data['tax_invoices'][0]);old.update(id=2,document_date='2026-08-01')
    data['tax_invoices'].append(old)
    line=deepcopy(data['tax_invoice_lines'][0]);line.update(id=2,tax_invoice_id=2,tax_recognition_event_id=999)
    data['tax_invoice_lines'].append(line)
    assert report(data)['matched']


def test_api_denies_other_company():
    from unittest.mock import AsyncMock, Mock
    from types import SimpleNamespace
    from fastapi import FastAPI
    from app.api.v1.vat_registers import router
    from app.api.deps import get_current_user
    from app.core.database import get_db
    local=FastAPI();local.include_router(router)
    db=AsyncMock();db.execute.return_value=Mock(scalar_one_or_none=lambda:None)
    local.dependency_overrides[get_current_user]=lambda:SimpleNamespace(id=1)
    local.dependency_overrides[get_db]=lambda:db
    response=TestClient(local).get('/companies/2/vat-registers',params=dict(date_from=str(START),date_to=str(END),as_of=NOW.isoformat()))
    assert response.status_code==403


@pytest.mark.asyncio
async def test_endpoint_never_flushes_or_commits(monkeypatch):
    from unittest.mock import AsyncMock
    from app.api.v1 import vat_registers as api
    fn=AsyncMock(return_value=report(fixture()))
    monkeypatch.setattr(api,'get_vat_register',fn)
    db=AsyncMock()
    result=await api.vat_register(1,START,END,NOW,None,None,None,db,None)
    assert result['matched']
    db.commit.assert_not_awaited();db.flush.assert_not_awaited();db.rollback.assert_not_awaited()
    assert fn.await_args.kwargs['company_id']==1


@pytest.mark.asyncio
async def test_snapshot_limit_fails_closed(monkeypatch):
    import json
    from unittest.mock import AsyncMock
    from app.services import vat_register_service as service
    monkeypatch.setattr(service,'MAX_ROWS',1)
    db=AsyncMock();db.scalar.return_value=json.dumps({'tax_invoices':[{'id':1},{'id':2}]})
    with pytest.raises(VatRegisterError,match='not truncated'):
        await service.load_snapshot(db,company_id=1)


def test_reversal_has_rk_and_exact_original_journal_link():
    data=fixture()
    original=data['tax_recognition_events'][0]
    reverse=deepcopy(original);reverse.update(id=2,reversal_of_id=1,recognition_date='2026-09-02')
    data['tax_recognition_events'].append(reverse)
    data['journal_entries'][0]['status']='reversed'
    data['journal_entries'].append(dict(id=2,company_id=1,tax_recognition_event_id=2,entry_date='2026-09-02',status='posted',created_at=STAMP,posted_at=STAMP,reversal_of_id=1))
    data['journal_entry_lines'] += [dict(id=3,journal_entry_id=2,account_id=1,debit='20',credit='0'),dict(id=4,journal_entry_id=2,account_id=3,debit='0',credit='20')]
    data['tax_invoice_corrections']=[dict(id=2,company_id=1,direction='output',document_number='RK1',document_date='2026-09-02',seller_vat_number='123',created_at=STAMP,original_tax_invoice_id=1)]
    data['tax_invoice_correction_lines']=[dict(id=2,company_id=1,tax_invoice_correction_id=2,line_number=1,source_kind='recognition_reversal',tax_recognition_reversal_event_id=2,taxable_base_delta='-100',tax_amount_delta='-20',tax_rate_code='VAT20')]
    data['tax_invoice_correction_registration_events']=[dict(id=2,company_id=1,tax_invoice_correction_id=2,status='registered',event_date='2026-09-02',created_at=STAMP)]
    result=report(data)
    assert result['matched'],result['issues']
    assert result['totals']['output']['expected_vat']==0
    assert result['totals']['output']['posted_vat']==0
    data['journal_entries'][1]['reversal_of_id']=None
    assert 'reversal_link_mismatch' in [i['code'] for i in report(data)['issues']]


def test_shifted_gl_period_reports_both_sides():
    data=fixture();data['journal_entries'][0]['entry_date']='2026-10-01'
    result=build_vat_register(data,company_id=1,date_from=date(2026,10,1),date_to=date(2026,10,31),as_of=NOW)
    assert result['totals']['output']['expected_vat']==0
    assert result['totals']['output']['posted_vat']==20
    assert 'journal_date_mismatch' in [i['code'] for i in result['issues']]


def test_wire_amounts_stay_decimal_strings():
    from app.schemas.vat_register import VatRegisterReport
    encoded=VatRegisterReport.model_validate(report(fixture())).model_dump(mode='json',by_alias=True)
    assert encoded['register'][0]['tax_amount']=='20.00'
    assert encoded['totals']['output']['expected_vat']=='20.00'
