"""Compose original payroll form previews using the pinned DPS schemas.

These bytes are for internal review, not filing. Structural validation is
separate from statutory source binding and independent mapping approval.
"""
from datetime import date
from decimal import Decimal
from hashlib import sha256
import re
import xml.etree.ElementTree as ET

from app.services.payroll_regulatory_form_fields import (
    FormFieldError, _date, _integer, field_contract, map_review_row,
)
from app.services.payroll_regulatory_xsd_contract import (
    FORMS, form_structure, validate_electronic_structure,
)


def _elements(node):
    if node['particle'] == 'element':
        yield node['attributes']['name']
    else:
        for child in node['children']:
            yield from _elements(child)


def _header(kind, values, month):
    fields = [f for f in field_contract(kind)['fields'] if f['section'] == 'header']
    computed = {'report_original', 'report_new', 'report_correction', 'report_year', 'report_month'}
    allowed = {f['key'] for f in fields} - computed
    if type(values) is not dict or set(values) - allowed:
        raise FormFieldError('Unknown or computed header fields supplied')
    result = {'HZ': '1', 'HZY': str(month.year), 'HZM': str(month.month)}
    optional = {'conditional_edrpou', 'optional_date', 'optional_text',
                'conditional_identity', 'conditional_text'}
    for field in fields:
        key, rule = field['key'], field['rule']
        if key in computed:
            continue
        value = values.get(key)
        if value is None:
            if rule not in optional:
                raise FormFieldError('Missing header field: ' + key)
            continue
        if rule in {'date', 'optional_date'}:
            output = _date(value, key).strftime('%d%m%Y')
        elif rule in {'positive_integer', 'nonnegative_integer'}:
            output = _integer(value, key, 1 if rule == 'positive_integer' else 0, 999999)
        else:
            if type(value) is not str or not value.strip() or value != value.strip():
                raise FormFieldError('Canonical header text required: ' + key)
            output = value
            pattern = None
            if rule in {'edrpou', 'conditional_edrpou'}:
                pattern = r'[0-9]{8}'
            elif rule in {'taxpayer_identity', 'conditional_identity'}:
                pattern = r'[0-9]{10}|[А-ЩЬЮЯЄІЇҐ]{2}[0-9]{6}|[0-9]{9}'
            elif key == 'territorial_code':
                pattern = r'UA[0-9]{17}'
            if pattern and re.fullmatch(pattern, value) is None:
                raise FormFieldError('Invalid header identifier: ' + key)
        result[field['xml_tag']] = output
    for left, right in [('accountant_identity', 'accountant_name'), ('agreement_date', 'agreement_number')]:
        if (values.get(left) is None) != (values.get(right) is None):
            raise FormFieldError('Paired header fields required: ' + left + ', ' + right)
    if _date(values['filing_date'], 'filing_date') < month:
        raise FormFieldError('Filing date precedes reporting month')
    return result


def build_original_form_preview(kind, *, reporting_month, header, rows, tax_office_code,
                                document_number=1):
    """Build all rows, derived totals and coherent transport/body headers.

    No totals or XML fragments are accepted from the caller. Correction
    documents must use their own original-row and reconciliation workflow.
    Empty forms are not inferred to be applicable. No database is mutated.
    """
    if type(reporting_month) is not str:
        raise FormFieldError('Reporting month required')
    month = _date(reporting_month + '-01', 'reporting_month')
    if month < date(2026, 7, 1):
        raise FormFieldError('Version 11 requires July 2026 or later')
    if type(rows) not in {list, tuple} or not rows or len(rows) > 10000:
        raise FormFieldError('One to 10000 source rows required')
    if type(tax_office_code) is not str or re.fullmatch(r'[0-9]{4}', tax_office_code) is None:
        raise FormFieldError('Four-digit tax office code required')
    number = _integer(document_number, 'document_number', 1, 999999)
    contract = field_contract(kind)
    scalar = _header(kind, header, month)
    mapped = [map_review_row(kind, row, reporting_month=reporting_month) for row in rows]
    totals = {}
    row_tags = {f['key']: f['xml_tag'] for f in contract['fields'] if f['section'] == 'rows'}
    for field in contract['fields']:
        if field['section'] != 'totals':
            continue
        tag = row_tags[field['key']]
        present = [row['xml_fields'][tag] for row in mapped if tag in row['xml_fields']]
        if present:
            amount = format(sum((Decimal(value) for value in present), Decimal('0.00')), '.2f')
            totals[field['key']] = amount
            scalar[field['xml_tag']] = amount
    transport = {
        'TIN': scalar['HTIN'], 'C_DOC': 'J05', 'C_DOC_SUB': FORMS[kind][3:6],
        'C_DOC_VER': '11', 'C_DOC_TYPE': '0', 'C_DOC_CNT': number,
        'C_REG': str(int(tax_office_code[:2])), 'C_RAJ': str(int(tax_office_code[2:])),
        'PERIOD_MONTH': str(month.month), 'PERIOD_TYPE': '1', 'PERIOD_YEAR': str(month.year),
        'C_STI_ORIG': tax_office_code, 'C_DOC_STAN': '1', 'D_FILL': scalar['HFILL'],
    }
    root = ET.Element('DECLAR')
    head = ET.SubElement(root, 'DECLARHEAD')
    body = ET.SubElement(root, 'DECLARBODY')
    structure = form_structure(kind)
    for tag in _elements(structure['header']):
        if tag in transport:
            ET.SubElement(head, tag).text = transport[tag]
    for tag in _elements(structure['body']):
        if tag in scalar:
            ET.SubElement(body, tag).text = scalar[tag]
        for index, row in enumerate(mapped, 1):
            if tag in row['xml_fields']:
                ET.SubElement(body, tag, ROWNUM=str(index)).text = row['xml_fields'][tag]
    document = ET.tostring(root, encoding='utf-8', xml_declaration=True)
    validation = validate_electronic_structure(kind, document)
    dependencies = {'REPORT_HEADER_SOURCE_BINDING', 'FORM_APPLICABILITY'}
    for row in mapped:
        dependencies.update(row['unresolved_dependencies'])
    return {
        **validation, 'status': 'INTERNAL_PREVIEW_NOT_FOR_FILING', 'xml': document,
        'row_count': len(mapped), 'totals': totals,
        'field_contract_sha256': mapped[0]['field_contract_sha256'],
        'unresolved_dependencies': sorted(dependencies),
        'document_sha256': sha256(document).hexdigest(),
    }
