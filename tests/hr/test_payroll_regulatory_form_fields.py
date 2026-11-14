from copy import deepcopy
from datetime import date
from hashlib import sha256
from pathlib import Path
import shutil
import pytest
from app.services import payroll_regulatory_form_fields as f


def row(kind):
    identity = {'citizenship': 1, 'insured_identity': '0012345678', 'first_name': 'Тест'}
    if kind == 'd1':
        return {**identity, 'gender': 'Ж', 'insured_category': '1', 'income_month': 10,
                'income_year': 2026, 'gross_accrued': '10000.00', 'usc_base': '10000.00',
                'usc_accrued': '2200.00', 'primary_employment': 1, 'part_time': 0,
                'special_service': 0, 'new_workplace': 0, 'nonfixed_work_time': 0}
    if kind == '4df':
        return {'taxpayer_identity': '0012345678', 'gross_accrued': '10000', 'gross_paid': '10000',
                'pit_accrued': '1800', 'pit_remitted': '1800', 'levy_accrued': '500',
                'levy_remitted': '500', 'income_code': '101'}
    if kind == 'd5':
        return {**identity, 'person_category': 1, 'event_start': '2026-10-01', 'transfer': 0,
                'professional_name': 'Бухгалтер', 'profession_code': '3433',
                'position_name': 'Бухгалтер', 'event_document': 'Наказ № 1 від 01.10.2026'}
    return {**identity, 'special_service_code': 'ЗПЗ013А1', 'start_day': 1, 'end_day': 31,
            'service_days': 20, 'normal_days': 22}


def project(kind, values=None, **kwargs):
    return f.map_review_row(kind, row(kind) if values is None else values,
                            reporting_month='2026-10', **kwargs)


@pytest.mark.parametrize('kind', ['d1','4df','d5','d6'])
def test_every_body_field_has_verified_official_pdf_location(kind):
    contract = f.field_contract(kind)
    root = Path(__file__).resolve().parents[2]
    pdf = root / 'docs/normative_evidence/2026_11_13/forms' / (contract['form_code']+'.pdf')
    assert sha256(pdf.read_bytes()).hexdigest() == contract['official_pdf_sha256']
    assert len({x['xml_tag'] for x in contract['fields']}) == len(contract['fields'])


def test_four_df_income_code_is_g05_not_g06_and_levy_is_not_pit():
    fields = project('4df')['xml_fields']
    assert fields['T1RXXXXG05'] == '101'
    assert fields['T1RXXXXG5A'] == '500.00'
    assert fields['T1RXXXXG04A'] == '1800.00'
    assert fields['T1RXXXXG03'] == '10000.00'
    assert 'T1RXXXXG06D' not in fields


@pytest.mark.parametrize('gender,encoded', [('Ж', '0'), ('Ч', '1')])
def test_d1_gender_uses_electronic_encoding_without_self_approval(gender, encoded):
    result = project('d1', {**row('d1'), 'gender': gender})
    assert result['xml_fields']['T1RXXXXG6'] == encoded
    assert 'D1_GENDER_ENCODING_OFFICIAL_CONFIRMATION' in result['unresolved_dependencies']
    assert result['official_generation'] == 'BLOCKED'


@pytest.mark.parametrize('kind', ['d1','4df','d5','d6'])
def test_projection_is_pure_and_never_self_approves(kind):
    data = row(kind); original = deepcopy(data)
    result = project(kind, data)
    assert data == original
    assert result['official_generation'] == 'BLOCKED'
    assert not result['statutory_mapping_approved']
    assert 'ERP_SOURCE_BINDING' in result['unresolved_dependencies']


@pytest.mark.parametrize('kind', ['d1','4df','d5','d6'])
def test_original_and_correction_markers_do_not_interchange(kind):
    data = {**row(kind), 'correction_marker': 0}
    with pytest.raises(f.FormFieldError, match='Original'):
        project(kind, data)
    assert project(kind, data, report_type='new')['xml_fields']


@pytest.mark.parametrize('value', ['NaN', 'Infinity', '1.001', True, 2.2])
def test_invalid_money_rejected(value):
    with pytest.raises(f.FormFieldError, match='money'):
        project('4df', {**row('4df'), 'gross_paid': value})


def test_identity_formats_are_form_specific_and_preserve_zeroes():
    assert project('d1')['xml_fields']['T1RXXXXG7S'] == '0012345678'
    assert project('d1', {**row('d1'), 'insured_identity': 'БКАА000001'})['xml_fields']['T1RXXXXG7S'] == 'БКАА000001'
    with pytest.raises(f.FormFieldError, match='identity'):
        project('4df', {**row('4df'), 'taxpayer_identity': 'БКАА000001'})
    with pytest.raises(f.FormFieldError, match='identity'):
        project('d1', {**row('d1'), 'insured_identity': 'АА000001'})


def test_d1_employment_without_income_does_not_invent_zero_salary():
    data = row('d1')
    for key in ('gross_accrued', 'usc_base', 'usc_accrued'): del data[key]
    data['employment_days'] = 31
    result = project('d1', data)
    assert 'T1RXXXXG16' not in result['xml_fields']
    assert result['xml_fields']['T1RXXXXG14'] == '31'


def test_d1_days_use_income_month_not_reporting_month():
    with pytest.raises(f.FormFieldError, match='income month'):
        project('d1', {**row('d1'), 'income_month': 2, 'sick_days': 29})


@pytest.mark.parametrize('patch', [
    {'civil_contract_with_employee': 0}, {'internal_secondary': 0},
    {'event_start': '2026-09-30'}, {'event_end':'2026-10-15'},
    {'professional_name': None}, {'person_category': 8},
])
def test_d5_conditional_fields_and_scope(patch):
    with pytest.raises(f.FormFieldError): project('d5', {**row('d5'), **patch})


def test_d5_transfer_does_not_require_termination_ground():
    fields=project('d5',{**row('d5'),'transfer':1,'event_end':'2026-10-15'})['xml_fields']
    assert fields['T1RXXXXG102D']=='15102026'


@pytest.mark.parametrize('patch', [
    {'service_hours': 8, 'service_minutes':0}, {'normal_days':None},
    {'service_days':32}, {'start_day':31,'end_day':1},
    {'attestation_order_number':'10'},
])
def test_d6_units_norm_and_attestation_pair(patch):
    with pytest.raises(f.FormFieldError): project('d6', {**row('d6'), **patch})


def test_d6_hours_minutes_are_not_decimal_hours():
    data=row('d6');data.pop('service_days');data.pop('normal_days')
    data.update(service_hours=150,service_minutes=30,normal_hours=176,normal_minutes=0)
    fields=project('d6',data)['xml_fields']
    assert fields['T1RXXXXG121']=='150' and fields['T1RXXXXG122']=='30'
    with pytest.raises(f.FormFieldError): project('d6',{**data,'service_minutes':60})


def test_corrupt_field_contract_does_not_approve_or_map(tmp_path, monkeypatch):
    shutil.copytree(f.ROOT,tmp_path/'forms')
    monkeypatch.setattr(f,'ROOT',tmp_path/'forms')
    (f.ROOT/'field_contract.json').write_text('{}')
    with pytest.raises(f.FormFieldError,match='fingerprint'): project('d1')


@pytest.mark.parametrize('month',['2026-06','2025-10','2026-13','2026-1'])
def test_no_silent_version_backdating(month):
    with pytest.raises(f.FormFieldError):
        f.map_review_row('d1',row('d1'),reporting_month=month)


def test_unknown_semantic_input_is_not_silently_ignored():
    with pytest.raises(f.FormFieldError,match='Unknown'):
        project('4df',{**row('4df'),'net_paid':7700})
