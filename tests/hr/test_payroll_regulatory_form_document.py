from copy import deepcopy
from hashlib import sha256
import xml.etree.ElementTree as ET

import pytest

from app.services.payroll_regulatory_form_document import build_original_form_preview
from app.services.payroll_regulatory_form_fields import FormFieldError
from tests.hr.test_payroll_regulatory_form_fields import row


def header(kind):
    values = dict(company_name='ТОВ «Тест & перевірка»', company_tax_number='00123456',
                  filing_date='2026-11-05', director_identity='0012345678', director_name='Тест')
    if kind == '4df':
        values.update(report_number=1, territorial_code='UA80000000000093317',
                      employment_count=2, civil_contract_count=0)
    return values


def build(kind, **kwargs):
    args = dict(reporting_month='2026-10', header=header(kind), rows=[row(kind)],
                tax_office_code='2601')
    args.update(kwargs)
    return build_original_form_preview(kind, **args)


@pytest.mark.parametrize('kind', ['d1', '4df', 'd5', 'd6'])
def test_complete_document_with_real_rows_passes_official_schema(kind):
    result = build(kind)
    assert result['structure_valid']
    assert result['document_sha256'] == sha256(result['xml']).hexdigest()
    assert result['official_generation'] == 'BLOCKED'
    assert 'ERP_SOURCE_BINDING' in result['unresolved_dependencies']
    root = ET.fromstring(result['xml'])
    assert root.findtext('DECLARHEAD/TIN') == '00123456'
    assert root.findtext('DECLARBODY/HNAME') == header(kind)['company_name']
    assert root.findtext('DECLARHEAD/D_FILL') == root.findtext('DECLARBODY/HFILL')


@pytest.mark.parametrize('kind,tag,key', [('d1', 'R01G16', 'gross_accrued'),
                                       ('4df', 'R01G03', 'gross_paid')])
def test_multiple_rows_are_numbered_and_summed_without_float_or_manual_totals(kind, tag, key):
    first, second = row(kind), row(kind)
    second['insured_identity' if kind == 'd1' else 'taxpayer_identity'] = '0012345679'
    first[key], second[key] = '0.10', '0.20'
    original = deepcopy([first, second])
    result = build(kind, rows=[first, second])
    assert [first, second] == original
    assert result['totals'][key] == '0.30'
    root = ET.fromstring(result['xml'])
    assert root.findtext('DECLARBODY/' + tag) == '0.30'
    assert {node.attrib['ROWNUM'] for node in root.find('DECLARBODY') if 'ROWNUM' in node.attrib} == {'1', '2'}


def test_d1_absent_income_is_not_written_as_zero():
    data = row('d1')
    for key in ('gross_accrued', 'usc_base', 'usc_accrued'):
        del data[key]
    data['employment_days'] = 31
    result = build('d1', rows=[data])
    assert result['totals'] == {}
    assert ET.fromstring(result['xml']).find('DECLARBODY/R01G16') is None


@pytest.mark.parametrize('patch', [
    {'company_tax_number': 12345678}, {'accountant_name': 'Тест'},
    {'report_original': 1}, {'gross_paid': '10000'}, {'filing_date': '2026-09-30'},
])
def test_header_cannot_override_generated_values_or_drop_identity(patch):
    with pytest.raises(FormFieldError):
        build('4df', header={**header('4df'), **patch})


def test_correction_cannot_masquerade_as_original():
    with pytest.raises(FormFieldError, match='Original'):
        build('4df', rows=[{**row('4df'), 'correction_marker': 1}])


def test_preview_is_deterministic_and_independent_of_subsequent_input_mutation():
    data = row('4df')
    result = build('4df', rows=[data])
    assert build('4df', rows=[data])['xml'] == result['xml']
    data['gross_paid'] = '0'
    assert result['totals']['gross_paid'] == '10000.00'


@pytest.mark.parametrize('kwargs', [{'rows': []}, {'tax_office_code': '2601X'},
                                  {'reporting_month': '2026-06'}, {'document_number': True}])
def test_invalid_scope_or_transport_rejected(kwargs):
    with pytest.raises(FormFieldError):
        build('d5', **kwargs)
