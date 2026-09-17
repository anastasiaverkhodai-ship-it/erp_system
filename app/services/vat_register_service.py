"""Company-scoped VAT register and source/GL control from one PostgreSQL snapshot.

Legal-document totals and recognized VAT are separate measures. All amounts stay
Decimal, including PostgreSQL JSON transport. No ORM writes or lifecycle calls.
"""
from collections import defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal
import json
from zoneinfo import ZoneInfo

from sqlalchemy import text

from app.services.accounting_account_roles import AccountingAccountRole as Role
from app.services.chart_of_accounts_template_types import ChartOfAccountsTemplateType
from app.services.ukrainian_chart_working_profiles import get_ukrainian_chart_working_profile

ZERO = Decimal('0.00')
MAX_ROWS = 10000
# Identifiers below are internal constants, never request input.
TABLES = (
    'tax_invoices', 'tax_invoice_lines', 'tax_invoice_registration_events',
    'tax_invoice_credit_evidence_links', 'tax_invoice_corrections',
    'tax_invoice_correction_lines', 'tax_invoice_correction_registration_events',
    'tax_calculations', 'tax_recognition_events', 'tax_credit_evidence',
    'sales_return_recognition_events', 'trade_value_correction_events',
    'purchase_return_vat_adjustment_events',
    'purchase_value_correction_vat_adjustment_events',
    'purchase_return_input_vat_credit_correction_events',
    'purchase_value_correction_input_vat_credit_correction_events',
    'journal_entries', 'accounts',
)
DECLARATION_TABLES = ('vat_declarations', 'vat_declaration_source_lines')


class VatRegisterError(ValueError):
    pass


def money(value):
    value = Decimal(str(value))
    if not value.is_finite():
        raise VatRegisterError('Non-finite amount in VAT register source')
    return value


def day(value):
    return value if isinstance(value, date) and not isinstance(value, datetime) else date.fromisoformat(str(value)[:10])


def instant(value, *, legacy_utc=True):
    value = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    if value.tzinfo is None:
        if not legacy_utc:
            raise VatRegisterError('VAT cutoff must include timezone')
        # Legacy journal/account columns are naive UTC (datetime.utcnow).
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def validate_request(company_id, date_from, date_to, as_of):
    if company_id <= 0 or date_to < date_from or (date_to-date_from).days > 366:
        raise VatRegisterError('Provide a company and a period of at most 367 days')
    return instant(as_of, legacy_utc=False)


async def load_snapshot(db, *, company_id, include_declaration=False):
    tables = list(TABLES)
    if include_declaration:
        available = await db.scalar(text("SELECT to_regclass('vat_declarations') IS NOT NULL AND to_regclass('vat_declaration_source_lines') IS NOT NULL"))
        if not available:
            raise VatRegisterError('Declaration schema is not installed')
        tables.extend(DECLARATION_TABLES)
    queries = {name: f'SELECT * FROM {name} WHERE company_id=:company_id ORDER BY id LIMIT :row_limit' for name in tables}
    queries['companies'] = 'SELECT * FROM companies WHERE id=:company_id'
    queries['journal_entry_lines'] = 'SELECT l.* FROM journal_entry_lines l JOIN journal_entries j ON j.id=l.journal_entry_id WHERE j.company_id=:company_id ORDER BY l.id LIMIT :row_limit'
    parts = [f"'{name}', (SELECT coalesce(json_agg(r), '[]'::json) FROM ({sql}) r)" for name, sql in queries.items()]
    # One statement supplies a single MVCC snapshot even under READ COMMITTED.
    raw = await db.scalar(text('SELECT json_build_object(' + ','.join(parts) + ')::text'),
                          {'company_id': company_id, 'row_limit': MAX_ROWS+1})
    snapshot = json.loads(raw, parse_float=Decimal)
    if any(len(rows) > MAX_ROWS for rows in snapshot.values()):
        raise VatRegisterError('VAT control exceeds 10000 rows per source; report was not truncated')
    return snapshot


async def get_vat_register(db, *, company_id, date_from, date_to, as_of,
                           direction=None, registration_status=None, declaration_id=None):
    as_of = validate_request(company_id, date_from, date_to, as_of)
    snapshot = await load_snapshot(db, company_id=company_id, include_declaration=declaration_id is not None)
    return build_vat_register(snapshot, company_id=company_id, date_from=date_from,
        date_to=date_to, as_of=as_of, direction=direction,
        registration_status=registration_status, declaration_id=declaration_id)


def build_vat_register(data, *, company_id, date_from, date_to, as_of,
                       direction=None, registration_status=None, declaration_id=None):
    as_of = validate_request(company_id, date_from, date_to, as_of)
    if direction not in (None, 'input', 'output'):
        raise VatRegisterError('Invalid direction')
    if registration_status not in (None, 'prepared', 'submitted', 'registered', 'suspended', 'rejected', 'unknown'):
        raise VatRegisterError('Invalid registration status')
    company = next((r for r in data.get('companies', []) if r['id'] == company_id), None)
    if company is None:
        raise VatRegisterError('Company not found')
    # Defence in depth for callers supplying a snapshot, beyond SQL scoping.
    for table, rows in data.items():
        if any(r.get('company_id', company_id) != company_id for r in rows):
            raise VatRegisterError(f'Cross-company row in {table}')
    visible = lambda r: not r.get('created_at') or instant(r['created_at']) <= as_of
    rows = {k: [r for r in values if visible(r)] for k, values in data.items()}
    indexes = {k: {r['id']: r for r in v} for k, v in rows.items()}
    in_period = lambda value: date_from <= day(value) <= date_to
    issues = []
    def issue(code, **refs):
        item = {'code': code, **refs}
        if item not in issues:
            issues.append(item)
    try:
        profile = get_ukrainian_chart_working_profile(ChartOfAccountsTemplateType(company['chart_of_accounts_template']))
        account_ids = {}
        for role in (Role.TAX_SETTLEMENT, Role.VAT_INPUT, Role.VAT_OUTPUT, Role.GOODS_REVENUE):
            code = profile.get_code_or_none(role)
            matches = [r for r in rows.get('accounts', []) if r['code'] == code]
            if len(matches) != 1:
                raise VatRegisterError(f'Unique account required for role {role.value}')
            account_ids[role] = matches[0]['id']
    except (ValueError, KeyError) as exc:
        raise VatRegisterError('VAT account configuration unavailable') from exc

    # Normalized economic/credit events; one key for each immutable source.
    sources = {}
    def source(kind, r, direction, effective, base, tax, journal_field=None, contra=None, link=None):
        sign = -1 if r.get('reversal_of_id') else 1
        sources[(kind, r['id'])] = dict(source_kind=kind, source_id=r['id'], direction=direction,
            effective_date=day(effective), taxable_base=money(base)*sign, tax_amount=money(tax)*sign,
            reversal_of_id=r.get('reversal_of_id'), journal_field=journal_field, contra=contra,
            legal_source=link, row=r)
    for r in rows.get('tax_recognition_events', []):
        calc = indexes.get('tax_calculations', {}).get(r['tax_calculation_id'])
        if not calc:
            issue('missing_calculation', source_kind='tax_recognition', source_id=r['id'],effective_date=r['recognition_date'])
            continue
        dr = calc['direction']
        if dr not in ('input','output'):
            raise VatRegisterError('Unsupported VAT direction in calculation')
        if calc.get('tax_type') != 'vat':
            continue
        kind = dr+'_tax_recognition'
        contra = Role.VAT_INPUT if dr == 'input' else (Role.GOODS_REVENUE if r.get('invoice_fulfillment_allocation_id') else Role.VAT_OUTPUT)
        source(kind, r, dr, r['recognition_date'], r['recognized_taxable_base'], r['recognized_tax_amount'], 'tax_recognition_event_id', contra)
    for r in rows.get('sales_return_recognition_events', []):
        source('sales_return', r, 'output', r['recognition_date'], -(money(r['returned_gross_amount'])-money(r['returned_tax_amount'])), -money(r['returned_tax_amount']))
    for r in rows.get('trade_value_correction_events', []):
        if r['direction'] == 'sale':
            tax = money(r['corrected_tax_amount'])-money(r['original_tax_amount'])
            base = money(r['corrected_gross_amount'])-money(r['original_gross_amount'])-tax
            source('sales_value_correction', r, 'output', r['correction_date'], base, tax)
    for r in rows.get('purchase_return_input_vat_credit_correction_events', []):
        source('purchase_return_input_credit_correction', r, 'input', r['adjustment_date'], -money(r['reduced_taxable_base']), -money(r['reduced_tax_amount']), 'purchase_return_input_vat_credit_correction_event_id', Role.VAT_INPUT, ('purchase_return', r['purchase_return_vat_adjustment_event_id']))
    for r in rows.get('purchase_value_correction_input_vat_credit_correction_events', []):
        sign = 1 if r['correction_kind'] == 'increase' else -1
        source('purchase_value_input_credit_correction', r, 'input', r['adjustment_date'], money(r['corrected_taxable_base'])*sign, money(r['corrected_tax_amount'])*sign, 'purchase_value_correction_input_vat_credit_correction_event_id', Role.VAT_INPUT, ('purchase_value_correction', r['purchase_value_correction_vat_adjustment_event_id']))

    # Legal correction sources can exceed credit actually claimed: do not equate them.
    legal = dict(sources)
    for table, kind in (('purchase_return_vat_adjustment_events', 'purchase_return'), ('purchase_value_correction_vat_adjustment_events', 'purchase_value_correction')):
        for r in rows.get(table, []):
            sign = (-1 if r.get('reversal_of_id') else 1) * (1 if r.get('adjustment_kind') == 'increase' else -1)
            legal[(kind,r['id'])] = dict(source_kind=kind,source_id=r['id'], direction='input', effective_date=day(r['adjustment_date']), taxable_base=money(r['adjusted_taxable_base'])*sign, tax_amount=money(r['adjusted_tax_amount'])*sign, row=r)

    register = []
    document_statuses = {}
    covered = defaultdict(list)
    evidence_docs = defaultdict(list)
    for r in rows.get('tax_invoice_credit_evidence_links', []):
        if r['tax_invoice_id'] in indexes.get('tax_invoices', {}):
            evidence_docs[r['tax_credit_evidence_id']].append(('PN',r['tax_invoice_id']))
    rk_map = {'sales_return': ('sales_return', 'sales_return_recognition_event_id'),
              'sales_value_correction': ('sales_value_correction', 'trade_value_correction_event_id'),
              'recognition_reversal': ('output_tax_recognition', 'tax_recognition_reversal_event_id'),
              'purchase_return': ('purchase_return', 'purchase_return_vat_adjustment_event_id'),
              'purchase_value_correction': ('purchase_value_correction', 'purchase_value_correction_vat_adjustment_event_id')}
    for doc_type, header_table, line_table, parent, history_table in (
        ('PN','tax_invoices','tax_invoice_lines','tax_invoice_id','tax_invoice_registration_events'),
        ('RK','tax_invoice_corrections','tax_invoice_correction_lines','tax_invoice_correction_id','tax_invoice_correction_registration_events')):
        grouped_lines = defaultdict(list)
        for line in rows.get(line_table, []):
            grouped_lines[line[parent]].append(line)
        histories = defaultdict(list)
        for event in rows.get(history_table, []):
            if day(event['event_date']) <= as_of.astimezone(ZoneInfo('Europe/Kyiv')).date():
                histories[event[parent]].append(event)
        for doc in rows.get(header_table, []):
            history = sorted(histories[doc['id']], key=lambda r: (r['event_date'], r['id']))
            status = history[-1]['status'] if history else 'unknown'
            document_statuses[(doc_type,doc['id'])] = status
            lines = []
            if doc.get('currency_code','UAH')!='UAH':
                issue('unsupported_currency',document_type=doc_type,document_id=doc['id'])
            for line in sorted(grouped_lines[doc['id']], key=lambda r:r['line_number']):
                base = money(line['taxable_base' if doc_type=='PN' else 'taxable_base_delta'])
                tax = money(line['tax_amount' if doc_type=='PN' else 'tax_amount_delta'])
                key = None
                if doc_type == 'PN' and doc['direction'] == 'output':
                    key = ('output_tax_recognition', line.get('tax_recognition_event_id'))
                elif doc_type == 'RK':
                    spec = rk_map.get(line['source_kind'])
                    if spec:
                        key = (spec[0], line.get(spec[1]))
                refs = dict(document_type=doc_type, document_id=doc['id'], line_id=line['id'])
                linked = legal.get(key)
                if key:
                    covered[key].append(refs)
                    if linked is None:
                        issue('missing_legal_source', **refs, source_kind=key[0], source_id=key[1])
                    elif (base,tax) != (linked['taxable_base'],linked['tax_amount']):
                        issue('document_source_amount_mismatch', **refs, source_kind=key[0], source_id=key[1])
                    elif linked['direction'] != doc['direction']:
                        issue('document_source_direction_mismatch', **refs)
                evidence_ids=[]
                if doc_type == 'PN' and doc['direction'] == 'input':
                    links = [r for r in rows.get('tax_invoice_credit_evidence_links',[]) if r['tax_invoice_id']==doc['id'] and r['tax_calculation_id']==line['tax_calculation_id']]
                    evidence_ids=[r['tax_credit_evidence_id'] for r in links]
                    evidences = [indexes.get('tax_credit_evidence',{}).get(r['tax_credit_evidence_id']) for r in links]
                    if not evidences or any(e is None for e in evidences):
                        issue('missing_credit_evidence', **refs)
                    elif (base,tax) != (sum((money(e['evidenced_taxable_base']) for e in evidences),ZERO),sum((money(e['evidenced_tax_amount']) for e in evidences),ZERO)):
                        issue('document_evidence_amount_mismatch', **refs)
                if doc_type == 'RK' and line.get('tax_credit_evidence_id'):
                    evidence_docs[line['tax_credit_evidence_id']].append(('RK',doc['id']))
                lines.append(dict(line_id=line['id'],line_number=line['line_number'],taxable_base=base,tax_amount=tax,
                    tax_rate_code=line['tax_rate_code'],source_kind=key[0] if key else 'input_evidence',source_id=key[1] if key else None,evidence_ids=evidence_ids))
            if in_period(doc['document_date']):
                if not lines:
                    issue('document_without_lines',document_type=doc_type,document_id=doc['id'])
                if status != 'registered':
                    issue('document_not_registered', document_type=doc_type, document_id=doc['id'],status=status)
                register.append(dict(document_type=doc_type,document_id=doc['id'],direction=doc['direction'],
                    document_number=doc['document_number'],document_date=day(doc['document_date']),
                    seller_vat_number=doc['seller_vat_number'],buyer_vat_number=doc.get('buyer_vat_number'),
                    original_tax_invoice_id=doc.get('original_tax_invoice_id'),registration_status=status,
                    registration_date=day(history[-1]['event_date']) if history else None,
                    taxable_base=sum((r['taxable_base'] for r in lines),ZERO),tax_amount=sum((r['tax_amount'] for r in lines),ZERO),lines=lines))
    for key,s in legal.items():
        if key[0] in ('purchase_return','purchase_value_correction') and in_period(s['effective_date']) and key not in covered and (s['taxable_base'] or s['tax_amount']):
            issue('missing_tax_document',source_kind=key[0],source_id=key[1])
    for key, refs in covered.items():
        if len(refs)>1:
            issue('duplicate_source_coverage',source_kind=key[0],source_id=key[1],documents=refs)

    journal_lines = defaultdict(list)
    for line in rows.get('journal_entry_lines', []):
        journal_lines[line['journal_entry_id']].append(line)
    journals = rows.get('journal_entries', [])
    journals_by_source = defaultdict(list)
    for j in journals:
        for field in ('tax_recognition_event_id','purchase_return_input_vat_credit_correction_event_id','purchase_value_correction_input_vat_credit_correction_event_id'):
            if j.get(field):
                journals_by_source[(field,j[field])].append(j)
    def is_posted(j):
        if j.get('posted_at'):
            return instant(j['posted_at']) <= as_of and j['status'] in ('posted','reversed')
        if j['status'] in ('posted','reversed'):
            issue('missing_posting_timestamp',journal_id=j['id'])
        return False
    tax_account = account_ids[Role.TAX_SETTLEMENT]
    checked_journals = set()
    event_rows = []
    for key,s in sources.items():
        attached = journals_by_source.get((s['journal_field'],s['source_id']),[]) if s['journal_field'] else []
        relevant = in_period(s['effective_date']) or any(in_period(j['entry_date']) for j in attached)
        if not relevant:
            continue
        refs = dict(source_kind=key[0],source_id=key[1])
        if s['row'].get('currency_code') != 'UAH':
            issue('unsupported_currency',**refs)
        legal_key = s.get('legal_source') or key
        documents = list(covered.get(legal_key,[]))
        if s['direction']=='input' and key[0]=='input_tax_recognition':
            ev_id=s['row'].get('tax_credit_evidence_id')
            evidence=indexes.get('tax_credit_evidence',{}).get(ev_id)
            if not evidence or evidence.get('tax_calculation_id',s['row']['tax_calculation_id'])!=s['row']['tax_calculation_id']:
                issue('invalid_credit_evidence',**refs,evidence_id=ev_id)
            elif evidence.get('credit_available_date') and day(evidence['credit_available_date'])>s['effective_date']:
                issue('credit_before_available_date',**refs,evidence_id=ev_id)
            documents = [dict(document_type=typ,document_id=i) for typ,i in evidence_docs.get(ev_id,[])]
            if not documents:
                evidence=indexes.get('tax_credit_evidence',{}).get(ev_id)
                if evidence and evidence.get('reversal_of_id'):
                    documents=[dict(document_type=typ,document_id=i) for typ,i in evidence_docs.get(evidence['reversal_of_id'],[])]
        for doc_ref in documents:
            if document_statuses.get((doc_ref['document_type'],doc_ref['document_id'])) != 'registered':
                issue('source_document_not_registered',**refs,**doc_ref)
        if not documents and (s['tax_amount'] or s['taxable_base']):
            issue('missing_tax_document',**refs)
        actual=ZERO
        if not s['journal_field'] and s['tax_amount']:
            # Existing commercial return journal deliberately excludes VAT; do not
            # mislabel it as the tax posting or fabricate a zero difference.
            issue('missing_vat_posting_contract',**refs)
        if s['journal_field']:
            if s['tax_amount'] and len(attached)!=1:
                issue('missing_journal' if not attached else 'duplicate_journal',**refs)
            if not s['tax_amount'] and attached:
                issue('unexpected_zero_tax_journal',**refs)
            for j in attached:
                checked_journals.add(j['id'])
                jrefs={**refs,'journal_id':j['id']}
                if day(j['entry_date'])!=s['effective_date']:
                    issue('journal_date_mismatch',**jrefs)
                posted=is_posted(j)
                if not posted:
                    issue('journal_not_posted_at_cutoff',**jrefs)
                amount=s['tax_amount'] * (1 if s['direction']=='output' else -1)
                expected={tax_account:-amount, account_ids[s['contra']]:amount}
                balances=defaultdict(lambda:ZERO)
                gross=defaultdict(lambda:[ZERO,ZERO])
                for line in journal_lines[j['id']]:
                    debit,credit=money(line['debit']),money(line['credit'])
                    balances[line['account_id']] += debit-credit
                    gross[line['account_id']][0] += debit
                    gross[line['account_id']][1] += credit
                expected_gross={aid:[max(v,ZERO),max(-v,ZERO)] for aid,v in expected.items()}
                if dict(gross)!=expected_gross:
                    issue('journal_accounts_or_amounts_mismatch',**jrefs)
                if s['reversal_of_id']:
                    originals=journals_by_source.get((s['journal_field'],s['reversal_of_id']),[])
                    if len(originals)!=1 or j.get('reversal_of_id')!=originals[0]['id']:
                        issue('reversal_link_mismatch',**jrefs)
                elif j.get('reversal_of_id'):
                    issue('unexpected_journal_reversal',**jrefs)
                if posted and in_period(j['entry_date']):
                    actual += -balances[tax_account] * (1 if s['direction']=='output' else -1)
        expected=s['tax_amount'] if in_period(s['effective_date']) else ZERO
        event_rows.append(dict(**refs,direction=s['direction'],effective_date=s['effective_date'],
            taxable_base=s['taxable_base'] if in_period(s['effective_date']) else ZERO,
            expected_vat=expected,posted_vat=actual,difference=actual-expected,
            documents=documents,journal_ids=[j['id'] for j in attached]))
    unclassified=ZERO
    for j in journals:
        if j['id'] in checked_journals or not in_period(j['entry_date']):
            continue
        tax_lines=[r for r in journal_lines[j['id']] if r['account_id']==tax_account]
        if tax_lines and is_posted(j):
            amount=sum((money(r['credit'])-money(r['debit']) for r in tax_lines),ZERO)
            unclassified+=amount
            issue('unclassified_tax_settlement_journal',journal_id=j['id'],credit_minus_debit=amount)
    totals={}
    for dr in ('output','input'):
        selected=[e for e in event_rows if e['direction']==dr]
        expected=sum((e['expected_vat'] for e in selected),ZERO)
        posted=sum((e['posted_vat'] for e in selected),ZERO)
        totals[dr]=dict(taxable_base=sum((e['taxable_base'] for e in selected),ZERO),expected_vat=expected,posted_vat=posted,difference=posted-expected)
    relevant_sources={(e['source_kind'],e['source_id']) for e in event_rows}
    relevant_sources.update(s['legal_source'] for s in sources.values() if s.get('legal_source') and (s['source_kind'],s['source_id']) in relevant_sources)
    relevant_sources.update(key for key,s in legal.items() if in_period(s['effective_date']))
    relevant_documents={(r['document_type'],r['document_id']) for r in register}
    for e in event_rows:
        relevant_documents.update((r['document_type'],r['document_id']) for r in e['documents'])
    relevant_journals={j['id'] for j in journals if in_period(j['entry_date'])}|checked_journals
    issues=[i for i in issues if (i.get('source_kind'),i.get('source_id')) in relevant_sources or (i.get('document_type'),i.get('document_id')) in relevant_documents or i.get('journal_id') in relevant_journals or (i.get('effective_date') and in_period(i['effective_date']))]
    declaration=None
    if declaration_id is not None:
        declaration=next((r for r in data.get('vat_declarations',[]) if r['id']==declaration_id),None)
        if not declaration:
            raise VatRegisterError('Declaration not found for company and cutoff')
        if day(declaration['period_start'])!=date_from or day(declaration['period_end'])!=date_to or instant(declaration['source_cutoff_at'])!=as_of:
            raise VatRegisterError('Use the declaration period and exact source_cutoff_at')
        decl_sources=[r for r in data.get('vat_declaration_source_lines',[]) if r['vat_declaration_id']==declaration_id]
        fields={'output_tax_recognition':'tax_recognition_event_id','input_tax_recognition':'tax_recognition_event_id',
                'sales_return':'sales_return_recognition_event_id','sales_value_correction':'trade_value_correction_event_id',
                'purchase_return_input_credit_correction':'purchase_return_input_vat_credit_correction_event_id',
                'purchase_value_input_credit_correction':'purchase_value_correction_input_vat_credit_correction_event_id'}
        seen=set()
        for line in decl_sources:
            key=(line['source_kind'],line.get(fields.get(line['source_kind'],'')))
            s=sources.get(key)
            if key in seen:
                issue('duplicate_declaration_source',declaration_id=declaration_id,line_id=line['id'])
            seen.add(key)
            if not s or not in_period(s['effective_date']) or (money(line['taxable_base_delta']),money(line['tax_amount_delta']),line['direction'],day(line['economic_effective_date']))!=(s['taxable_base'],s['tax_amount'],s['direction'],s['effective_date']):
                issue('declaration_source_mismatch',declaration_id=declaration_id,line_id=line['id'],source_kind=key[0],source_id=key[1])
        for key,s in sources.items():
            if in_period(s['effective_date']) and key not in seen:
                issue('missing_declaration_source',declaration_id=declaration_id,source_kind=key[0],source_id=key[1])
        for dr,base_field,tax_field in (('output','output_taxable_base','output_vat'),('input','input_taxable_base','input_vat_credit')):
            if (money(declaration[base_field]),money(declaration[tax_field]))!=(totals[dr]['taxable_base'],totals[dr]['expected_vat']):
                issue('declaration_header_mismatch',declaration_id=declaration_id,direction=dr)
        declaration={'id':declaration_id,'checked':True}
    register=sorted((r for r in register if (direction is None or r['direction']==direction) and (registration_status is None or r['registration_status']==registration_status)),key=lambda r:(r['document_date'],r['document_type'],r['document_id']))
    # Filters affect the visible register only; they cannot hide reconciliation errors.
    rate_totals={}
    for doc in register:
        for line in doc['lines']:
            key=(doc['direction'],line['tax_rate_code'])
            total=rate_totals.setdefault(key,dict(direction=key[0],tax_rate_code=key[1],taxable_base=ZERO,tax_amount=ZERO))
            total['taxable_base']+=line['taxable_base']
            total['tax_amount']+=line['tax_amount']
    register_totals={dr:dict(taxable_base=sum((r['taxable_base'] for r in register if r['direction']==dr),ZERO),tax_amount=sum((r['tax_amount'] for r in register if r['direction']==dr),ZERO)) for dr in ('output','input')}
    return dict(company_id=company_id,date_from=date_from,date_to=date_to,as_of=as_of,
        account_roles={role.value:aid for role,aid in account_ids.items()},
        account_basis='current_company_profile',register=register,register_totals=register_totals,
        events=event_rows,totals=totals,rate_totals=list(rate_totals.values()),unclassified_tax_settlement=unclassified,
        declaration=declaration,matched=not issues,issues=issues)
