"""Freeze prospective net payment attribution in the posting transaction.

The payment fixes settlement allocation, not tax remittance. Legacy payments
without primary allocations are never silently assigned proportional taxes.
"""
import json
from datetime import date, timedelta
from decimal import Decimal
from hashlib import sha256
from sqlalchemy import select, or_
from app.models.company import Company
from app.models.payroll import PayrollCalculation, PayrollCalculationLine, PayrollPeriod
from app.models.payroll_statutory import PayrollStatutoryResult, PayrollStatutoryResultLine
from app.models.payroll_disbursement import PayrollDisbursement
from app.models.payroll_advance import PayrollAdvance
from app.models.payroll_paid_income import PayrollPaidIncome
from app.models.journal_entry import JournalEntry
from app.services.payroll_regulatory_payment_evidence import active_posted_journal_query
from app.services.payroll_advance_tax_source_proof import verify_automatic_advance_tax_source_in_db
from app.services.payroll_paid_income_calculation import allocate_remaining_net, money, PaidIncomeError, paid_income_month


def canonical(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)


def allocation_from_record(record):
    try:
        envelope = json.loads(record.snapshot_json)
        body = envelope['body']
        if sha256(canonical(body).encode()).hexdigest() != envelope['sha256']:
            raise ValueError
        if body['schema'] != 'PAYROLL_PAID_INCOME_V1' or body['company_id'] != record.company_id or body['payment_id'] != record.payroll_disbursement_id or body['journal_id'] != record.journal_entry_id:
            raise ValueError
        part = body['allocation']
        if set(part) != {'gross','pit','levy','net'} or money(part['gross']) != money(part['net']) + money(part['pit']) + money(part['levy']):
            raise ValueError
        if body['policy'] != 'PROSPECTIVE_REMAINING_NET_SHARE_V1' or body['tax_remittance_inferred'] is not False:
            raise ValueError
        expected = allocate_remaining_net(
            **body['monthly_source'], previous=body['previous_allocations'],
            cash=body['cash'], non_tax_withheld=body['non_tax_withheld'])
        if part != expected:
            raise ValueError
        if body.get('component_evidence') is not None:
            from app.services.payroll_paid_income_components import verify_component_evidence
            components = verify_component_evidence(body['component_evidence'])
            if money(components['gross']) != money(part['gross']):
                raise ValueError
        date.fromisoformat(body['payment_date'])
        date.fromisoformat(body['accrual_month'] + '-01')
        return body
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise PaidIncomeError('Corrupt paid-income snapshot') from exc


from app.services.payroll_mutation_guard import serialized_payroll_mutation


async def salary_deadline(db, *, company_id, contract_id, nominal):
    from app.models.time_attendance import EmploymentScheduleAssignment, WorkScheduleDay
    for offset in range(8):
        day = nominal - timedelta(days=offset)
        assignments = list((await db.scalars(select(EmploymentScheduleAssignment).where(
            EmploymentScheduleAssignment.company_id==company_id,
            EmploymentScheduleAssignment.employment_contract_id==contract_id,
            EmploymentScheduleAssignment.effective_from<=day,
            or_(EmploymentScheduleAssignment.effective_to.is_(None),EmploymentScheduleAssignment.effective_to>=day)))).all())
        if len(assignments)!=1:
            return None
        working = await db.scalar(select(WorkScheduleDay).where(WorkScheduleDay.company_id==company_id,
            WorkScheduleDay.work_schedule_id==assignments[0].work_schedule_id,WorkScheduleDay.weekday==day.isoweekday()))
        if working is None:
            return None
        if working.is_working_day:
            return dict(date=day.isoformat(),assignment_id=assignments[0].id,schedule_day_id=working.id)
    return None


@serialized_payroll_mutation(PaidIncomeError)
async def freeze_paid_income(db, *, company_id, payment, journal):
    """Called under the existing company payroll lock after journal posting."""
    if payment.company_id != company_id or journal.company_id != company_id or journal.payroll_disbursement_id != payment.id:
        raise PaidIncomeError('Paid-income source scope mismatch')
    if not await db.scalar(active_posted_journal_query(company_id=company_id,journal_entry_id=journal.id)):
        raise PaidIncomeError('Active posted payment journal required')
    if payment.payroll_calculation_id is None:
        return None  # Opening debt needs its original income/tax source.
    existing = await db.scalar(select(PayrollPaidIncome).where(
        PayrollPaidIncome.company_id==company_id, PayrollPaidIncome.payroll_disbursement_id==payment.id))
    if existing:
        return allocation_from_record(existing)
    calculation = await db.scalar(select(PayrollCalculation).where(
        PayrollCalculation.company_id==company_id, PayrollCalculation.id==payment.payroll_calculation_id))
    if calculation is None:
        raise PaidIncomeError('Payroll source not found')
    result = await db.scalar(select(PayrollStatutoryResult).where(
        PayrollStatutoryResult.company_id==company_id, PayrollStatutoryResult.payroll_calculation_id==calculation.id))
    if result is None or result.currency_code != 'UAH' or payment.currency_code != 'UAH':
        raise PaidIncomeError('UAH statutory source required')
    lines = list((await db.scalars(select(PayrollStatutoryResultLine).where(
        PayrollStatutoryResultLine.company_id==company_id,
        PayrollStatutoryResultLine.payroll_statutory_result_id==result.id))).all())
    totals = {k: sum((money(x.amount) for x in lines if x.component==component),Decimal('0.00'))
              for k,component in [('pit','personal_income_tax'),('levy','military_levy')]}
    if money(result.gross_amount) != money(result.net_amount)+totals['pit']+totals['levy']:
        raise PaidIncomeError('Monthly statutory source does not reconcile')
    previous, sources, component_previous = [], [], []
    component_issue = None
    prior = list((await db.scalars(select(PayrollDisbursement).where(
        PayrollDisbursement.company_id==company_id,
        PayrollDisbursement.payroll_calculation_id==calculation.id,
        PayrollDisbursement.id!=payment.id).order_by(PayrollDisbursement.id))).all())
    for old in prior:
        if old.cancelled_at or old.reversed_on or not await db.scalar(active_posted_journal_query(
                company_id=company_id,payroll_disbursement_id=old.id)):
            continue
        record = await db.scalar(select(PayrollPaidIncome).where(
            PayrollPaidIncome.company_id==company_id,PayrollPaidIncome.payroll_disbursement_id==old.id))
        if record is None:
            return None  # Unknown historical split must not block a legitimate cash payment.
        body = allocation_from_record(record)
        if money(body['allocation']['net']) != money(old.amount)+money(old.withheld_amount):
            raise PaidIncomeError('Prior payment allocation no longer matches payment')
        previous.append(body['allocation']); sources.append({'payment_id':old.id,'snapshot_id':record.id})
        if body.get('component_evidence'):
            component_previous.append(dict(settlement_id=f'payment:{old.id}',
                slices=body['component_evidence']['body']['slices']))
        else:
            component_issue = 'Prior payment has no verified component allocation'
    advances = list((await db.scalars(select(PayrollAdvance).where(
        PayrollAdvance.company_id==company_id,PayrollAdvance.payroll_period_id==calculation.payroll_period_id,
        PayrollAdvance.employment_contract_id==calculation.employment_contract_id))).all())
    for advance in advances:
        jid = await db.scalar(select(JournalEntry.id).where(JournalEntry.company_id==company_id,
            JournalEntry.payroll_advance_id==advance.id,JournalEntry.reversal_of_id.is_(None)))
        if not jid or not await db.scalar(active_posted_journal_query(company_id=company_id,journal_entry_id=jid)):
            continue
        if not advance.calculation_snapshot_json:
            return None
        proof = await verify_automatic_advance_tax_source_in_db(db,advance)
        taxes = {c.component:c.amount for c in proof.components}
        previous.append(dict(gross=str(proof.gross_amount),net=str(proof.net_amount),
            pit=str(taxes['personal_income_tax']),levy=str(taxes['military_levy'])))
        sources.append({'advance_id':advance.id,'journal_id':jid})
    allocation = allocate_remaining_net(gross=result.gross_amount,pit=totals['pit'],levy=totals['levy'],
        previous=previous,cash=payment.amount,non_tax_withheld=payment.withheld_amount)
    period = await db.scalar(select(PayrollPeriod).where(PayrollPeriod.company_id==company_id,PayrollPeriod.id==calculation.payroll_period_id))
    from app.models.employment_contract import EmploymentContract
    contract = await db.get(EmploymentContract, calculation.employment_contract_id)
    if contract is None or contract.company_id != company_id or contract.employee_id != payment.employee_id:
        raise PaidIncomeError('Payment employee differs from payroll source')
    from app.services.payroll_paid_income_components import load_component_sources, component_evidence, allocate_components
    components = None
    try:
        component_sources = await load_component_sources(db, company_id=company_id, calculation=calculation, period=period)
        # Advances consume salary components once; no sick/vacation balance is
        # silently reclassified as an advance.
        for previous_source, previous_amount in zip(sources, previous):
            if 'advance_id' in previous_source:
                slices = allocate_components(sources=component_sources, previous=component_previous,
                    gross=previous_amount['gross'], salary_only=True)
                component_previous.append(dict(settlement_id=f"advance:{previous_source['advance_id']}", slices=slices))
        if component_issue is None:
            components = component_evidence(sources=component_sources, previous=component_previous, gross=allocation['gross'])
    except ValueError as exc:
        # Existing financial posting remains available for legacy sources;
        # missing dated evidence is explicit and cannot authorize form output.
        component_issue = str(exc)
    company = await db.get(Company,company_id)
    month = period.start_date.strftime('%Y-%m')
    following = date(period.start_date.year+(period.start_date.month==12),period.start_date.month%12+1,1)
    nominal_due = following.replace(day=company.salary_payment_day)
    # Preserve nominal deadline; working-calendar adjustment must be verified
    # separately before using the next-month salary exception.
    deadline = await salary_deadline(db,company_id=company_id,contract_id=calculation.employment_contract_id,nominal=nominal_due)
    types = sorted(set((await db.scalars(select(PayrollCalculationLine.line_type).where(
        PayrollCalculationLine.company_id==company_id,PayrollCalculationLine.payroll_calculation_id==calculation.id))).all()))
    body = dict(schema='PAYROLL_PAID_INCOME_V1',company_id=company_id,payment_id=payment.id,
        journal_id=journal.id,calculation_id=calculation.id,statutory_result_id=result.id,
        statutory_line_ids=sorted(x.id for x in lines),allocation=allocation,
        previous_sources=sources,previous_allocations=previous,
        monthly_source=dict(gross=str(result.gross_amount),pit=str(totals['pit']),levy=str(totals['levy'])),
        cash=str(payment.amount),non_tax_withheld=str(payment.withheld_amount),payment_date=payment.payment_date.isoformat(),accrual_month=month,
        nominal_salary_due_date=nominal_due.isoformat(),salary_deadline=deadline,income_line_types=types,
        policy='PROSPECTIVE_REMAINING_NET_SHARE_V1',tax_remittance_inferred=False,
        component_evidence=components,component_issue=component_issue)
    record = PayrollPaidIncome(company_id=company_id,payroll_disbursement_id=payment.id,journal_entry_id=journal.id,
        snapshot_json=canonical({'body':body,'sha256':sha256(canonical(body).encode()).hexdigest()}))
    db.add(record)
    await db.flush()
    return body


SALARY_TYPES = {'salary', 'supplement', 'minimum_wage'}


def report_month_for_record(body):
    types = set(body['income_line_types'])
    day = date.fromisoformat(body['payment_date'])
    month = body['accrual_month']
    # The same-month outcome does not depend on the next-month salary exception.
    accrual_start = date.fromisoformat(month + '-01')
    following = date(accrual_start.year + (accrual_start.month == 12), accrual_start.month % 12 + 1, 1)
    if day < following or (day.year, day.month) != (following.year, following.month):
        return day.strftime('%Y-%m')
    if types == {'severance'}:
        return day.strftime('%Y-%m')
    if not types or not types <= SALARY_TYPES:
        raise PaidIncomeError('Income classification and payment deadline required for this source')
    proof = body.get('salary_deadline')
    return paid_income_month(accrual_month=month,payment_date=day,salary=True,
        contractual_due_date=date.fromisoformat(proof['date']) if proof else None)


async def read_paid_income(db, *, company_id, payroll_calculation_id):
    """Read automatic paid gross, with explicit incomplete legacy-source errors.

    Calendar grouping is separate from budget remittances and official form
    approval. No caller amounts, tax allocations or approval flags are inputs.
    """
    from app.services.payroll_regulatory_withholding_source_reconciliation import build_source_reconciliation
    source = (await build_source_reconciliation(db,company_id=company_id,
        payroll_calculation_id=payroll_calculation_id))['body']
    review = source['reconciliation']
    calculation = await db.scalar(select(PayrollCalculation).where(PayrollCalculation.company_id==company_id,
        PayrollCalculation.id==payroll_calculation_id))
    period = await db.get(PayrollPeriod,calculation.payroll_period_id)
    month = period.start_date.strftime('%Y-%m')
    rows, issues, unknown = [], [], []
    known_net = Decimal('0.00')
    known_gross = Decimal('0.00')
    for payment in review['payments']:
        record = await db.scalar(select(PayrollPaidIncome).where(PayrollPaidIncome.company_id==company_id,
            PayrollPaidIncome.payroll_disbursement_id==payment['payment_id']))
        if record is None:
            unknown.append(payment)
            continue
        body = allocation_from_record(record)
        if body['calculation_id']!=calculation.id or body['payment_date']!=payment['payment_date'] or money(body['allocation']['net'])!=money(payment['net_disbursed'])+money(payment['non_tax_withheld']):
            raise PaidIncomeError('Persisted paid-income source mismatch')
        await validate_live_paid_income(db, company_id=company_id, calculation=calculation,
            period=period, record=record, body=body)
        known_net += money(body['allocation']['net']);known_gross += money(body['allocation']['gross'])
        try:
            attributed_month=report_month_for_record(body)
        except PaidIncomeError as exc:
            attributed_month=None;issues.append(dict(payment_id=payment['payment_id'],reason=str(exc)))
        rows.append(dict(source='payment',source_ids=[payment['payment_id']],payment_date=body['payment_date'],
            reporting_month=attributed_month,gross_paid=body['allocation']['gross'],snapshot_id=record.id,
            accrual_month=month,actual_payment_month=body['payment_date'][:7],tax_remittance_period=None,
            component_evidence=body.get('component_evidence'),component_issue=body.get('component_issue'),
            status='REVIEW_ONLY',official_generation='BLOCKED',
            classification_reason='Official income classification and form mapping require independent verification'))
    advances=list((await db.scalars(select(PayrollAdvance).where(PayrollAdvance.company_id==company_id,
        PayrollAdvance.payroll_period_id==calculation.payroll_period_id,
        PayrollAdvance.employment_contract_id==calculation.employment_contract_id))).all())
    for advance in advances:
        jid=await db.scalar(select(JournalEntry.id).where(JournalEntry.company_id==company_id,
            JournalEntry.payroll_advance_id==advance.id,JournalEntry.reversal_of_id.is_(None)))
        if not jid or not await db.scalar(active_posted_journal_query(company_id=company_id,journal_entry_id=jid)):
            continue
        if not advance.calculation_snapshot_json:
            issues.append(dict(advance_id=advance.id,reason='Historical advance has no gross/tax source'))
            continue
        proof=await verify_automatic_advance_tax_source_in_db(db,advance)
        known_net+=proof.net_amount;known_gross+=proof.gross_amount
        rows.append(dict(source='advance',source_ids=[advance.id],payment_date=proof.payment_date,
            reporting_month=proof.payment_date[:7],gross_paid=format(proof.gross_amount,'.2f')))
    if unknown:
        residual_net=money(review['statutory_net'])-known_net
        unknown_net=sum((money(p['net_disbursed'])+money(p['non_tax_withheld']) for p in unknown),Decimal('0.00'))
        months={p['payment_date'][:7] for p in unknown}
        # Exact aggregate only. Do not invent a split of historic partial payments.
        if not issues and residual_net==unknown_net and months=={month}:
            residual_gross=money(review['gross_accrued'])-known_gross
            if residual_gross<0:raise PaidIncomeError('Gross settlement exceeds payroll source')
            rows.append(dict(source='exact_legacy_aggregate',source_ids=sorted(p['payment_id'] for p in unknown),
                payment_date=None,reporting_month=month,gross_paid=format(residual_gross,'.2f')))
        else:
            issues.append(dict(payment_ids=sorted(p['payment_id'] for p in unknown),
                reason='Historical partial payment gross or reporting deadline cannot be uniquely reconstructed'))
    totals={}
    for row in rows:
        if row['reporting_month'] is not None:
            key=row['reporting_month'];totals[key]=totals.get(key,Decimal('0.00'))+money(row['gross_paid'])
    return dict(company_id=company_id,payroll_calculation_id=calculation.id,
        status='COMPLETE' if not issues else 'INCOMPLETE_SOURCE_DATA',rows=rows,
        gross_paid_by_month={k:format(v,'.2f') for k,v in sorted(totals.items())},issues=issues,
        budget_remittances_used_as_withholding=False,official_form_approved=False)


async def project_paid_income_month(db, *, company_id, reporting_month):
    """Read column-3 evidence across accrual periods, including overdue payroll.

    This is a financial review, not a completed statutory row: taxpayer identity,
    legal income code and correction markers belong to the form mapping layer.
    """
    from app.services.payroll_regulatory_withholding_source_reconciliation import WithholdingReconciliationError
    from app.services.payroll_advance_tax_source_proof import AdvanceTaxSourceProofError
    try:
        start = date.fromisoformat(reporting_month + '-01')
        if start.strftime('%Y-%m') != reporting_month:
            raise ValueError
    except (ValueError, TypeError):
        raise PaidIncomeError('Canonical reporting month required') from None
    following = date(start.year + (start.month == 12), start.month % 12 + 1, 1)
    # Include the following month's payments only for this month's accruals.
    # Older payroll paid this month must not disappear from the monthly review.
    calculations = list((await db.scalars(select(PayrollCalculation).join(
        PayrollPeriod, (PayrollPeriod.company_id == PayrollCalculation.company_id) &
        (PayrollPeriod.id == PayrollCalculation.payroll_period_id)).where(
            PayrollCalculation.company_id == company_id,
            or_(
                PayrollCalculation.id.in_(select(PayrollDisbursement.payroll_calculation_id).where(
                    PayrollDisbursement.company_id == company_id,
                    PayrollDisbursement.payment_date >= start,
                    PayrollDisbursement.payment_date < following)),
                (PayrollPeriod.start_date >= start) & (PayrollPeriod.start_date < following),
                select(PayrollAdvance.id).where(
                    PayrollAdvance.company_id == company_id,
                    PayrollAdvance.payroll_period_id == PayrollCalculation.payroll_period_id,
                    PayrollAdvance.employment_contract_id == PayrollCalculation.employment_contract_id,
                    PayrollAdvance.payment_date >= start,
                    PayrollAdvance.payment_date < following).exists()
            )))).all())
    rows, issues = [], []
    for calculation in calculations:
        try:
            review = await read_paid_income(db, company_id=company_id, payroll_calculation_id=calculation.id)
        except (PaidIncomeError, WithholdingReconciliationError, AdvanceTaxSourceProofError) as exc:
            issues.append(dict(payroll_calculation_id=calculation.id, reason=str(exc)))
            continue
        for row in review['rows']:
            if row['reporting_month'] == reporting_month:
                rows.append(dict(**row, payroll_calculation_id=calculation.id,
                    employment_contract_id=calculation.employment_contract_id))
        # Unknown attribution can affect the requested month. Never silently
        # turn a partial total into a complete column-3 value.
        issues.extend(dict(payroll_calculation_id=calculation.id, **issue) for issue in review['issues'])
    # An advance can be paid before a monthly calculation exists.
    advance_ids = {row['source_ids'][0] for row in rows if row['source'] == 'advance'}
    for advance in (await db.scalars(select(PayrollAdvance).where(
            PayrollAdvance.company_id == company_id, PayrollAdvance.payment_date >= start,
            PayrollAdvance.payment_date < following))).all():
        if advance.id in advance_ids:
            continue
        jid = await db.scalar(select(JournalEntry.id).where(JournalEntry.company_id == company_id,
            JournalEntry.payroll_advance_id == advance.id, JournalEntry.reversal_of_id.is_(None)))
        if not jid or not await db.scalar(active_posted_journal_query(company_id=company_id,journal_entry_id=jid)):
            continue
        try:
            proof = await verify_automatic_advance_tax_source_in_db(db, advance)
        except AdvanceTaxSourceProofError as exc:
            issues.append(dict(advance_id=advance.id,reason=str(exc)))
            continue
        rows.append(dict(source='advance',source_ids=[advance.id],payment_date=proof.payment_date,
            reporting_month=reporting_month,gross_paid=format(proof.gross_amount,'.2f'),
            payroll_calculation_id=None,employment_contract_id=advance.employment_contract_id))
    for payment in (await db.scalars(select(PayrollDisbursement).where(
            PayrollDisbursement.company_id == company_id, PayrollDisbursement.payroll_calculation_id.is_(None),
            PayrollDisbursement.payment_date >= start, PayrollDisbursement.payment_date < following))).all():
        if await db.scalar(active_posted_journal_query(company_id=company_id,payroll_disbursement_id=payment.id)):
            issues.append(dict(payment_id=payment.id,reason='Opening payroll debt requires original gross and tax evidence'))
    unique = {}
    for row in rows:
        key = (row['source'],tuple(row['source_ids']))
        if key in unique:
            prior = unique[key]
            if any(prior[field] != row[field] for field in ('gross_paid','reporting_month','employment_contract_id')):
                raise PaidIncomeError('Conflicting paid-income attribution across payroll calculations')
            continue
        unique[key] = row
    rows = list(unique.values())
    total = sum((money(row['gross_paid']) for row in rows), Decimal('0.00'))
    rows.sort(key=lambda row:(row['employment_contract_id'],row['payroll_calculation_id'] or 0,row['source'],row['source_ids']))
    return dict(company_id=company_id,reporting_month=reporting_month,rows=rows,
        verified_gross_paid_total=format(total,'.2f'),
        status='INCOMPLETE_SOURCE_DATA' if issues else 'COMPLETE',issues=issues,
        official_form_approved=False, tax_remittances_included_in_gross=False,
        review_status='REVIEW_ONLY',official_generation='BLOCKED',
        allocation_digest=sha256(canonical(rows).encode()).hexdigest())


async def validate_live_paid_income(db, *, company_id, calculation, period, record, body):
    """A self-consistent hash is not proof of accounting source identity."""
    from app.models.employment_contract import EmploymentContract
    payment = await db.get(PayrollDisbursement, record.payroll_disbursement_id)
    contract = await db.get(EmploymentContract, calculation.employment_contract_id)
    result = await db.scalar(select(PayrollStatutoryResult).where(
        PayrollStatutoryResult.company_id == company_id,
        PayrollStatutoryResult.payroll_calculation_id == calculation.id))
    if (payment is None or contract is None or result is None
            or payment.company_id != company_id or contract.company_id != company_id
            or payment.employee_id != contract.employee_id
            or payment.payroll_calculation_id != calculation.id
            or body['statutory_result_id'] != result.id
            or body['accrual_month'] != period.start_date.strftime('%Y-%m')
            or money(body['cash']) != money(payment.amount)
            or money(body['non_tax_withheld']) != money(payment.withheld_amount)):
        raise PaidIncomeError('Persisted paid-income identity differs from live source')
    lines = list((await db.scalars(select(PayrollStatutoryResultLine).where(
        PayrollStatutoryResultLine.company_id == company_id,
        PayrollStatutoryResultLine.payroll_statutory_result_id == result.id))).all())
    expected = dict(gross=money(result.gross_amount),
        pit=sum((money(x.amount) for x in lines if x.component == 'personal_income_tax'), Decimal('0.00')),
        levy=sum((money(x.amount) for x in lines if x.component == 'military_levy'), Decimal('0.00')))
    if (sorted(x.id for x in lines) != body['statutory_line_ids']
            or any(money(body['monthly_source'][k]) != v for k, v in expected.items())):
        raise PaidIncomeError('Persisted paid-income amounts differ from live statutory source')
    journal = await db.scalar(active_posted_journal_query(company_id=company_id,
        journal_entry_id=record.journal_entry_id))
    if not journal:
        raise PaidIncomeError('Paid-income posted journal is missing or reversed')
    if body.get('component_evidence'):
        from app.services.payroll_paid_income_components import load_component_sources
        live = await load_component_sources(db, company_id=company_id, calculation=calculation, period=period)
        if live != body['component_evidence']['body']['sources']:
            raise PaidIncomeError('Persisted paid-income components differ from live source')
