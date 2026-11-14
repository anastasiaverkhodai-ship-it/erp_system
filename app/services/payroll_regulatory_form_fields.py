"""Official field locations and conservative payroll row validation.

The physical form mapping is verified against DPS labelled PDFs and XSDs.
This does not authorize filing: source attribution, dated dictionaries and
report-level reconciliation remain independent requirements.
"""
from calendar import monthrange
from datetime import date
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import re

from app.services.payroll_regulatory_xsd_contract import ROOT, FORMS, form_structure

CONTRACT_SHA256 = '0bdd53a23de2ee0837c64cfe9400427b844fe20097510031cfa9c75ef1ac7faa'


class FormFieldError(ValueError):
    pass


def field_contract(kind):
    if type(kind) is not str or kind not in FORMS:
        raise FormFieldError('Unsupported form')
    raw = (ROOT / 'field_contract.json').read_bytes()
    if sha256(raw).hexdigest() != CONTRACT_SHA256:
        raise FormFieldError('Field contract fingerprint mismatch')
    result = json.loads(raw)['forms'][kind]
    # Validate actual packaged schemas as well as the separate field contract.
    structure = form_structure(kind)
    def names(node):
        own = {node['attributes']['name']} if node['particle'] == 'element' else set()
        for child in node['children']:
            own |= names(child)
        return own
    if {x['xml_tag'] for x in result['fields']} != names(structure['body']):
        raise FormFieldError('Incomplete field contract')
    return result


def _money(value, field):
    if isinstance(value, (bool, float)) or not isinstance(value, (str, int, Decimal)):
        raise FormFieldError('Invalid money: ' + field)
    try:
        amount = Decimal(value)
        if not amount.is_finite() or amount != amount.quantize(Decimal('.01')):
            raise InvalidOperation
    except (InvalidOperation, ValueError):
        raise FormFieldError('Invalid money: ' + field) from None
    return format(amount, '.2f')


def _date(value, field):
    if type(value) is date:
        return value
    if type(value) is str:
        try:
            parsed = date.fromisoformat(value)
            if parsed.isoformat() == value:
                return parsed
        except ValueError:
            pass
    raise FormFieldError('Canonical date required: ' + field)


def _integer(value, field, lower, upper):
    if type(value) is not int or not lower <= value <= upper:
        raise FormFieldError('Invalid integer: ' + field)
    return str(value)


def map_review_row(kind, values, *, reporting_month, report_type='original'):
    """Map semantic row fields to official XML tags without writing a report.

    Returns unresolved normative dependencies explicitly. A caller-supplied
    category/basis code is never upgraded to an independently approved code.
    Unknown keys fail, rather than silently dropping payroll information.
    """
    first = _date(reporting_month + '-01', 'reporting_month') if type(reporting_month) is str else None
    if first is None or first < date(2026, 7, 1):
        raise FormFieldError('Version 11 requires a reporting month from July 2026')
    if type(report_type) is not str or report_type not in {'original', 'new', 'correction'}:
        raise FormFieldError('Invalid report type')
    fields = [f for f in field_contract(kind)['fields'] if f['section'] == 'rows']
    allowed = {f['key'] for f in fields}
    if type(values) is not dict or set(values) - allowed:
        raise FormFieldError('Unknown semantic row fields')
    normalized = {}
    dependencies = {'ERP_SOURCE_BINDING', 'REPORT_RECONCILIATION'}
    days = monthrange(first.year, first.month)[1]
    optional = {'optional_text', 'conditional_text', 'optional_date', 'optional_money',
                'optional_days', 'conditional_integer', 'conditional_minutes',
                'conditional_binary', 'accrual_type', 'tax_relief_code',
                'historical_only', 'military_only', 'correction'}
    for f in fields:
        key, rule = f['key'], f['rule']
        value = values.get(key)
        if value is None:
            if rule not in optional:
                raise FormFieldError('Missing required field: ' + key)
            continue
        if rule in {'historical_only', 'military_only'}:
            raise FormFieldError('Outside supported payroll scope: ' + key)
        if rule in {'money', 'optional_money'}:
            output = _money(value, key)
        elif rule in {'binary', 'conditional_binary', 'correction'}:
            output = _integer(value, key, 0, 1)
        elif rule == 'gender':
            if type(value) is not str or value not in {'Ж', 'Ч'}:
                raise FormFieldError('Gender must use the form code Ж or Ч')
            # Paper labels are letters; the official XSD uses DGOzn (0/1).
            # Art-Zvit's XML/DBF field documentation identifies ST ->
            # T1RXXXXG6 as 0=female, 1=male. Keep separate filing approval.
            # https://docs.art-zvit.com.ua/8_20_2_3_structure_dbf.htm
            output = {'Ж': '0', 'Ч': '1'}[value]
            dependencies.add('D1_GENDER_ENCODING_OFFICIAL_CONFIRMATION')
        elif rule in {'month', 'year', 'day', 'optional_days', 'conditional_integer', 'conditional_minutes'}:
            lower, upper = {'month': (1, 12), 'year': (2026, 9999), 'day': (1, days),
                            'optional_days': (0, 31), 'conditional_integer': (0, 9999),
                            'conditional_minutes': (0, 59)}[rule]
            output = _integer(value, key, lower, upper)
        elif rule == 'optional_date':
            output = _date(value, key).strftime('%d%m%Y')
        elif rule in {'insured_identity', 'taxpayer_identity'}:
            if type(value) is not str:
                raise FormFieldError('Identity must preserve leading zeroes')
            pattern = r'[0-9]{10}'
            if rule == 'insured_identity':
                pattern += r'|БК[А-ЩЬЮЯЄІЇҐ]{2}[0-9]{6}|П[0-9]{9}'
            else:
                pattern += r'|[А-ЩЬЮЯЄІЇҐ]{2}[0-9]{6}|[0-9]{9}'
            if re.fullmatch(pattern, value) is None:
                raise FormFieldError('Invalid form-specific identity')
            output = value
            dependencies.add('DATED_REVIEWED_IDENTITY')
        elif rule == 'person_category':
            output = _integer(value, key, 1, 9)
            if value in {7, 8}:
                raise FormFieldError('Military categories outside private payroll scope')
            dependencies.add('D5_EVENT_CATEGORY_SOURCE')
        elif rule == 'accrual_type':
            if type(value) is not int or value not in {1, 2, 3, 8, 9, 10, 11, 12, 13, 14, 15}:
                raise FormFieldError('Unknown D1 accrual type')
            if value in {8, 9, 15}:
                raise FormFieldError('Historical or military accrual outside supported scope')
            output = str(value)
            dependencies.add('D1_ACCRUAL_TYPE_APPLICATION')
        else:
            if type(value) is not str or not value.strip() or value != value.strip():
                raise FormFieldError('Nonempty canonical text required: ' + key)
            output = value
            if rule in {'insured_category', 'income_code', 'tax_relief_code', 'special_service_code'}:
                pattern = {'insured_category': r'[1-9][0-9]{0,2}',
                           'income_code': r'[0-9]{3}',
                           'tax_relief_code': r'[0-9]{2}',
                           'special_service_code': r'[А-ЯЄІЇҐA-Z0-9]{8}'}[rule]
                if re.fullmatch(pattern, value) is None:
                    raise FormFieldError('Invalid code format: ' + key)
                dependencies.add('DATED_DICTIONARY:' + rule)
        normalized[f['xml_tag']] = output
    marker = values.get('correction_marker')
    if report_type == 'original' and marker is not None:
        raise FormFieldError('Original report must not contain a correction marker')
    if report_type != 'original' and marker is None and kind != 'd1':
        raise FormFieldError('Correction row requires an explicit insertion/deletion marker')
    if kind == 'd1':
        income_days = monthrange(values['income_year'], values['income_month'])[1]
        if any(values.get(k, 0) is not None and values.get(k, 0) > income_days
               for k in ('sick_days', 'unpaid_leave_days', 'employment_days', 'maternity_days')):
            raise FormFieldError('D1 days exceed the income month')
        if report_type == 'new' and marker is None:
            raise FormFieldError('New D1 report requires a correction marker')
        if report_type == 'correction':
            dependencies.add('D1_MONETARY_VERSUS_NONMONETARY_CORRECTION')
        dependencies.add('D1_BASE_CAP_MINIMUM_AND_CATEGORY_RECONCILIATION')
    elif kind == 'd5':
        category = values['person_category']
        if values.get('civil_contract_with_employee') is not None and category != 3:
            raise FormFieldError('D5 column 06 is only applicable to category 3')
        if values.get('internal_secondary') is not None and category != 2:
            raise FormFieldError('D5 column 11 is only applicable to category 2')
        dates = [_date(values[k], k) for k in ('event_start', 'event_end') if values.get(k) is not None]
        if not dates or any((d.year, d.month) != (first.year, first.month) for d in dates):
            raise FormFieldError('D5 requires an event in the reporting month')
        if len(dates) == 2 and dates[0] > dates[1]:
            raise FormFieldError('Rehire after termination requires separate D5 rows')
        if category in {1, 2}:
            if any(not values.get(k) for k in ('professional_name', 'profession_code', 'position_name')):
                raise FormFieldError('D5 employment requires profession and position')
            dependencies.add('DATED_D5_PROFESSION_AND_HR_EVENT')
        if category in {1, 2, 3, 9} and values.get('event_end') and not values.get('termination_ground') and values.get('transfer') != 1:
            raise FormFieldError('D5 termination requires a documented legal ground')
    elif kind == 'd6':
        if values['start_day'] > values['end_day']:
            raise FormFieldError('Reversed D6 interval')
        day_mode = values.get('service_days') is not None
        hour_mode = any(values.get(k) is not None for k in ('service_hours', 'service_minutes'))
        if day_mode == hour_mode:
            raise FormFieldError('D6 requires exactly one duration unit')
        if day_mode:
            if (not values.get('normal_days') or values['service_days'] <= 0
                    or values['service_days'] > values['end_day'] - values['start_day'] + 1
                    or any(values.get(k) is not None for k in ('normal_hours', 'normal_minutes'))):
                raise FormFieldError('D6 days and monthly norm do not reconcile')
        else:
            required = ('service_hours', 'service_minutes', 'normal_hours', 'normal_minutes')
            if any(values.get(k) is None for k in required) or values.get('normal_days') is not None:
                raise FormFieldError('D6 hours require hours/minutes and a matching monthly norm')
            minutes = values['service_hours'] * 60 + values['service_minutes']
            if not 0 < minutes <= (values['end_day'] - values['start_day'] + 1) * 24 * 60 or values['normal_hours'] * 60 + values['normal_minutes'] <= 0:
                raise FormFieldError('Invalid D6 duration or norm')
        if bool(values.get('attestation_order_number')) != bool(values.get('attestation_order_date')):
            raise FormFieldError('D6 attestation order number and date must be paired')
        dependencies.add('D6_BASIS_ATTESTATION_AND_HR_COVERAGE')
    else:
        dependencies.add('4DF_GROSS_PAYMENT_AND_TAX_PERIOD_ATTRIBUTION')
        for k in ('hire_date', 'termination_date'):
            if values.get(k) is not None:
                d = _date(values[k], k)
                if (d.year, d.month) != (first.year, first.month):
                    raise FormFieldError('4DF employment date outside reporting month')
    return {'form_code': FORMS[kind], 'xml_fields': normalized,
            'field_contract_sha256': CONTRACT_SHA256,
            'unresolved_dependencies': sorted(dependencies),
            'statutory_mapping_approved': False, 'official_generation': 'BLOCKED'}
