"""Prospective component allocation against authoritative payroll earnings.

Amounts are gross settlement amounts already established by the posting flow.
This module does not derive gross from cash or classify official income codes.
"""
from datetime import date
from decimal import Decimal
from hashlib import sha256
import json

from sqlalchemy import select
from app.models.payroll import PayrollCalculationLine
from app.services.payroll_paid_income_calculation import PaidIncomeError, money

ZERO = Decimal('0.00')
SALARY_TYPES = {'salary', 'supplement', 'minimum_wage'}


def digest(value):
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                            separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def source_key(row):
    return (row['accrual_month'], row['calculation_line_id'], row['funding'])


def allocate_components(*, sources, previous, gross, salary_only=False):
    """Oldest month, then source line and funding; exact residuals, no rounding.

    `previous` contains unique immutable settlement identities and their slices.
    Reversal handling belongs to the posted-source loader, never caller flags.
    """
    available = {}
    scope = set()
    for row in sources:
        if type(row) is not dict:
            raise PaidIncomeError('Invalid component source')
        for key in ('company_id', 'employment_contract_id', 'calculation_id', 'calculation_line_id'):
            if type(row.get(key)) is not int or row[key] <= 0:
                raise PaidIncomeError('Invalid component identity')
        scope.add((row['company_id'], row['employment_contract_id']))
        try:
            month = date.fromisoformat(row['accrual_month'] + '-01')
        except (ValueError, TypeError, KeyError):
            raise PaidIncomeError('Invalid component accrual month') from None
        if month.strftime('%Y-%m') != row['accrual_month'] or row.get('funding') not in {'employer', 'insurer'}:
            raise PaidIncomeError('Invalid component period or funding')
        key = source_key(row)
        if key in available or money(row['gross']) < 0:
            raise PaidIncomeError('Duplicate or negative component source')
        available[key] = [row, money(row['gross'])]
    if len(scope) != 1:
        raise PaidIncomeError('Component company or employee mismatch')
    settlements = set()
    for settlement in previous:
        identifier = settlement['settlement_id']
        if not isinstance(identifier, str) or not identifier or identifier in settlements:
            raise PaidIncomeError('Reused settlement allocation')
        settlements.add(identifier)
        seen = set()
        for part in settlement['slices']:
            key = source_key(part)
            if key not in available or key in seen:
                raise PaidIncomeError('Unrelated or duplicate previous component')
            seen.add(key)
            source, remaining = available[key]
            if any(part.get(field) != source[field] for field in source if field != 'gross'):
                raise PaidIncomeError('Previous component identity mismatch')
            used = money(part['gross'])
            if used < 0 or used > remaining:
                raise PaidIncomeError('Component allocation exceeds source balance')
            available[key][1] -= used
    requested = money(gross)
    if requested < 0:
        raise PaidIncomeError('Negative component settlement requires reversal evidence')
    result = []
    for key in sorted(available):
        source, remaining = available[key]
        if salary_only and source['income_type'] not in SALARY_TYPES:
            continue
        used = min(remaining, requested)
        if used:
            result.append(dict(source, gross=format(used, '.2f')))
            requested -= used
    if requested:
        raise PaidIncomeError('Component allocation exceeds source balance')
    return result


async def load_component_sources(db, *, company_id, calculation, period):
    """Read the same earning rows and dated leave sources used by payroll."""
    if calculation.company_id != company_id or period.company_id != company_id or calculation.payroll_period_id != period.id:
        raise PaidIncomeError('Component source period mismatch')
    lines = list((await db.scalars(select(PayrollCalculationLine).where(
        PayrollCalculationLine.company_id == company_id,
        PayrollCalculationLine.payroll_calculation_id == calculation.id
    ).order_by(PayrollCalculationLine.id))).all())
    result = []
    for line in lines:
        if line.currency_code != 'UAH' or money(line.amount) < 0:
            raise PaidIncomeError('Signed or non-UAH source needs reviewed component allocation')
        if not line.amount:
            continue
        month = period.start_date.strftime('%Y-%m')
        amounts = {(month, 'employer'): money(line.amount)}
        if line.line_type in {'vacation_pay', 'sick_pay', 'leave_compensation', 'severance'}:
            from app.services.payroll_dated_income_days import income_days
            days = await income_days(db, company_id=company_id, calculation=calculation, line=line, period=period)
            amounts = {}
            for day, value in days.items():
                key = (day.strftime('%Y-%m'), 'employer')
                amounts[key] = amounts.get(key, ZERO) + money(value)
            if line.line_type == 'sick_pay':
                from app.models.payroll_sick_leave import PayrollSickLeaveCalculation
                from app.services.payroll_sick_allocation import DATED_ALLOCATION_VERSIONS
                sick = await db.get(PayrollSickLeaveCalculation, line.source_sick_leave_calculation_id)
                if sick.rule_version not in DATED_ALLOCATION_VERSIONS:
                    raise PaidIncomeError('Legacy sick funding needs documented daily component allocation')
                amounts = {}
                for part in sick.insurance_assessment['daily_allocation']:
                    day = date.fromisoformat(part['date'])
                    if period.start_date <= day <= period.end_date:
                        key = (day.strftime('%Y-%m'), part['payer'])
                        amounts[key] = amounts.get(key, ZERO) + money(part['amount'])
        if sum(amounts.values(), ZERO) != money(line.amount):
            raise PaidIncomeError('Component dates do not reconcile to earning source')
        provenance = {name: getattr(line, name) for name in (
            'source_salary_rate_id', 'source_supplement_id', 'source_vacation_calculation_id',
            'source_sick_leave_calculation_id', 'source_termination_severance_id',
            'source_leave_compensation_line_id', 'source_leave_compensation_extra_id')}
        for (month, funding), value in sorted(amounts.items()):
            if value:
                result.append(dict(company_id=company_id, employment_contract_id=calculation.employment_contract_id,
                    calculation_id=calculation.id, calculation_line_id=line.id, accrual_month=month,
                    income_type=line.line_type, funding=funding, provenance=provenance, gross=format(value, '.2f')))
    if sum((money(r['gross']) for r in result), ZERO) != money(calculation.gross_amount):
        raise PaidIncomeError('Component sources do not reconcile to payroll gross')
    return sorted(result, key=source_key)


def component_evidence(*, sources, previous, gross):
    slices = allocate_components(sources=sources, previous=previous, gross=gross)
    body = dict(policy='PROSPECTIVE_COMPONENT_FIFO_V1', sources=sources,
                previous=previous, slices=slices, gross=format(money(gross), '.2f'))
    return dict(body=body, sha256=digest(body))


def verify_component_evidence(evidence):
    try:
        body = evidence['body']
        if body['policy'] != 'PROSPECTIVE_COMPONENT_FIFO_V1' or digest(body) != evidence['sha256']:
            raise ValueError
        expected = component_evidence(sources=body['sources'], previous=body['previous'], gross=body['gross'])
        if evidence != expected:
            raise ValueError
        return body
    except (ValueError, KeyError, TypeError) as exc:
        raise PaidIncomeError('Corrupt component settlement evidence') from exc
