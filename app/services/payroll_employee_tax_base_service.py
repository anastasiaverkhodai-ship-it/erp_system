"""Monthly employee tax bases, one benefit and one USC limit across contracts."""
from decimal import Decimal
from datetime import timedelta
from sqlalchemy import select, or_
from app.models.employment_contract import EmploymentContract
from app.models.payroll import PayrollCalculation
from app.models.payroll_statutory import PayrollStatutoryComponent as Component
from app.services.payroll_revision_service import current_calculation_filter, require_current_calculation


async def require_uniform_base_policy(db, *, company_id, period, contract, component, profile, rule):
    """Reject a monthly snapshot that would silently use only the last day's policy."""
    from app.models.payroll_tax import PayrollEmployeeTaxProfile, PayrollStatutoryBaseRule
    from app.services.payroll_statutory_service import (
        PayrollStatutoryValidationError as Error,
        resolve_payroll_employee_tax_profile, resolve_payroll_statutory_base_rule,
    )
    start = max(period.start_date, contract.start_date)
    end = min(period.end_date, contract.end_date or period.end_date)
    profiles = list((await db.scalars(select(PayrollEmployeeTaxProfile).where(
        PayrollEmployeeTaxProfile.company_id == company_id,
        PayrollEmployeeTaxProfile.employee_id == contract.employee_id,
        PayrollEmployeeTaxProfile.employment_contract_id == contract.id,
        PayrollEmployeeTaxProfile.effective_from <= end,
        or_(PayrollEmployeeTaxProfile.effective_to.is_(None), PayrollEmployeeTaxProfile.effective_to >= start),
    ))).all())
    rules = list((await db.scalars(select(PayrollStatutoryBaseRule).where(
        PayrollStatutoryBaseRule.company_id == company_id,
        PayrollStatutoryBaseRule.component == component.value,
        or_(PayrollStatutoryBaseRule.employment_kind.is_(None), PayrollStatutoryBaseRule.employment_kind == contract.employment_kind),
        PayrollStatutoryBaseRule.effective_from <= end,
        or_(PayrollStatutoryBaseRule.effective_to.is_(None), PayrollStatutoryBaseRule.effective_to >= start),
    ))).all())
    boundaries = {start, end}
    for policy in [*profiles, *rules]:
        if start <= policy.effective_from <= end:
            boundaries.add(policy.effective_from)
        if policy.effective_to and start <= policy.effective_to < end:
            boundaries.add(policy.effective_to + timedelta(days=1))
    expected = (profile.id if profile else None, rule.id if rule else None)
    for day in boundaries:
        current_profile = await resolve_payroll_employee_tax_profile(db, company_id=company_id,
            employee_id=contract.employee_id, employment_contract_id=contract.id, effective_date=day)
        current_rule = await resolve_payroll_statutory_base_rule(db, company_id=company_id,
            component=component, employment_kind=contract.employment_kind,
            tax_profile_category=current_profile.category if current_profile else None, effective_date=day)
        actual = (current_profile.id if current_profile else None, current_rule.id if current_rule else None)
        if actual != expected:
            raise Error('Tax profile or base policy changes within the income period; dated allocation is required')


async def require_uniform_rate(db, *, company_id, period, contract, component, rate):
    from app.models.payroll_statutory import PayrollStatutoryRate
    from app.services.payroll_statutory_service import (
        PayrollStatutoryValidationError as Error, resolve_statutory_rate,
    )
    start = max(period.start_date, contract.start_date)
    end = min(period.end_date, contract.end_date or period.end_date)
    candidates = list((await db.scalars(select(PayrollStatutoryRate).where(
        PayrollStatutoryRate.company_id == company_id,
        PayrollStatutoryRate.component == component.value,
        or_(PayrollStatutoryRate.employee_id.is_(None), PayrollStatutoryRate.employee_id == contract.employee_id),
        PayrollStatutoryRate.effective_from <= end,
        or_(PayrollStatutoryRate.effective_to.is_(None), PayrollStatutoryRate.effective_to >= start),
    ))).all())
    boundaries = {start, end}
    for candidate in candidates:
        if start <= candidate.effective_from <= end:
            boundaries.add(candidate.effective_from)
        if candidate.effective_to and start <= candidate.effective_to < end:
            boundaries.add(candidate.effective_to + timedelta(days=1))
    resolved_ids = set()
    for day in sorted(boundaries):
        resolved = await resolve_statutory_rate(db, company_id=company_id,
            component=component, employee_id=contract.employee_id, effective_date=day)
        resolved_ids.add(resolved.id)
        if Decimal(resolved.rate) != Decimal(rate) or len(resolved_ids) > 1:
            raise Error('Tax rate changes within employee income; dated allocation is required')


async def employee_component_base(db, *, company_id, period, calculation, contract,
                                  component, rate, profile, rule):
    from app.services.payroll_statutory_service import (
        PayrollStatutoryValidationError as Error, calculate_statutory_component_base,
        resolve_payroll_employee_tax_profile, resolve_payroll_statutory_base_rule,
        round_money,
    )
    if (contract.status == 'cancelled' or contract.start_date > period.end_date
            or (contract.end_date is not None and contract.end_date < period.start_date)):
        raise Error('Employment contract does not cover this payroll income period')
    await require_current_calculation(db, calculation=calculation, error_type=Error)
    if calculation.currency_code != 'UAH':
        raise Error('Employee taxation requires UAH earnings before applying statutory bases')
    contracts = list((await db.scalars(select(EmploymentContract).where(
        EmploymentContract.company_id == company_id, EmploymentContract.employee_id == contract.employee_id,
        EmploymentContract.status != 'cancelled', EmploymentContract.start_date <= period.end_date,
        or_(EmploymentContract.end_date.is_(None), EmploymentContract.end_date >= period.start_date),
    ).order_by(EmploymentContract.id))).all())
    normal = calculate_statutory_component_base(gross_amount=Decimal(calculation.gross_amount),profile=profile,rule=rule)
    if len(contracts) <= 1:
        contexts = [(contract, calculation, rule, normal, profile)]
    else:
        if any(c.employment_kind not in {'primary', 'internal_secondary', 'external_secondary'} for c in contracts):
            raise Error('Classify all employee contracts before aggregate monthly taxation')
        calculations = list((await db.scalars(select(PayrollCalculation).where(
            PayrollCalculation.company_id == company_id, PayrollCalculation.payroll_period_id == period.id,
            PayrollCalculation.employment_contract_id.in_([c.id for c in contracts]), current_calculation_filter(),
        ))).all())
        by_contract = {c.employment_contract_id: c for c in calculations}
        if len(calculations) != len(contracts) or set(by_contract) != {c.id for c in contracts}:
            raise Error('Calculate all employee contracts for the month before statutory taxation')
        if by_contract[contract.id].id != calculation.id:
            raise Error('Use the current employee calculation revision')
        if any(c.currency_code != 'UAH' for c in calculations):
            raise Error('Aggregate employee taxation requires UAH earnings')
        contexts = []
        for employment in contracts:
            tax_profile = await resolve_payroll_employee_tax_profile(db,company_id=company_id,
                employee_id=contract.employee_id,employment_contract_id=employment.id,
                effective_date=min(period.end_date, employment.end_date or period.end_date))
            base_rule = await resolve_payroll_statutory_base_rule(db,company_id=company_id,component=component,
                employment_kind=employment.employment_kind,
                tax_profile_category=tax_profile.category if tax_profile else None,
                effective_date=min(period.end_date, employment.end_date or period.end_date))
            values = calculate_statutory_component_base(gross_amount=Decimal(by_contract[employment.id].gross_amount),
                profile=tax_profile,rule=base_rule)
            contexts.append((employment,by_contract[employment.id],base_rule,values,tax_profile))
    for employment,source,base_rule,values,tax_profile in contexts:
        await require_uniform_rate(db,company_id=company_id,period=period,contract=employment,
            component=component,rate=rate)
        await require_uniform_base_policy(db,company_id=company_id,period=period,contract=employment,
            component=component,profile=tax_profile,rule=base_rule)
    ordered = sorted(contexts,key=lambda item:(item[0].employment_kind != 'primary',item[0].id))
    total_gross = sum((Decimal(c.gross_amount) for e,c,r,v,p in ordered),Decimal(0))
    metadata = dict(employee_gross_amount=total_gross)
    beneficiaries = [item for item in ordered if item[2] and item[2].base_mode == 'gross_after_benefit'
                     and item[4] and item[4].category == 'benefit_eligible']
    if beneficiaries and component != Component.PERSONAL_INCOME_TAX:
        raise Error('Social benefit applies to personal income tax only')
    values_by_contract = {e.id:v for e,c,r,v,p in ordered}
    if beneficiaries:
        if any(v[4] for e,c,r,v,p in ordered):
            raise Error('Mixed exempt and benefit income requires an explicit employee allocation')
        parameters = set()
        for e,c,r,v,p in beneficiaries:
            limit = p.benefit_income_limit_override if p.benefit_income_limit_override is not None else r.benefit_income_limit
            benefit = p.benefit_amount_override if p.benefit_amount_override is not None else r.benefit_amount
            if limit is None or limit <= 0:
                raise Error('Social benefit requires an explicit monthly employee income limit')
            if r.minimum_base_amount is not None or r.maximum_base_amount is not None:
                raise Error('Social benefit cannot use USC-style minimum or maximum bases')
            parameters.add((Decimal(benefit),Decimal(limit)))
        if len(parameters) != 1:
            raise Error('Conflicting employee social-benefit entitlements')
        benefit,limit = next(iter(parameters))
        available = min(benefit,total_gross) if total_gross <= limit else Decimal(0)
        selected_rule,selected_profile = beneficiaries[0][2],beneficiaries[0][4]
        metadata.update(benefit_income_limit_applied=limit,source_tax_profile_id=selected_profile.id,
            source_base_rule_id=selected_rule.id,base_rule_code=selected_rule.rule_code,
            base_rule_version=selected_rule.rule_version)
        for e,c,r,v,p in ordered:
            applied = min(Decimal(c.gross_amount),available)
            available -= applied
            values_by_contract[e.id]=(round_money(Decimal(c.gross_amount)-applied),applied,None,None,False)
    elif component == Component.UNIFIED_SOCIAL_CONTRIBUTION and len(ordered)>1:
        maxima = {Decimal(r.maximum_base_amount) for e,c,r,v,p in ordered
                  if not v[4] and r and r.maximum_base_amount is not None}
        if len(maxima)>1:
            raise Error('Conflicting employee-wide USC maximum bases')
        ceiling=next(iter(maxima),None)
        primary=[item for item in ordered if item[0].employment_kind=='primary']
        if len(primary)>1:
            raise Error('Multiple primary contracts in one month require dated USC allocation')
        floor=Decimal(0);floor_contract=None
        if primary:
            employment,source,base_rule,values,tax_profile=primary[0]
            if base_rule and base_rule.minimum_base_amount is not None and not values[4] and Decimal(rate)==Decimal('.22'):
                if employment.start_date>period.start_date or (employment.end_date and employment.end_date<period.end_date):
                    raise Error('Partial-month primary employment requires dated USC minimum eligibility')
                floor=Decimal(base_rule.minimum_base_amount);floor_contract=employment.id
        bases={e.id:Decimal(0) if v[4] else Decimal(c.gross_amount) for e,c,r,v,p in ordered}
        total=sum(bases.values(),Decimal(0))
        if 0<total<floor:
            bases[floor_contract]+=floor-total
        if ceiling is not None and floor>ceiling:
            raise Error('Employee USC minimum exceeds maximum')
        remaining=ceiling
        for e,c,r,v,p in ordered:
            base=bases[e.id]
            if remaining is not None:
                base=min(base,max(Decimal(0),remaining));remaining-=base
            values_by_contract[e.id]=(round_money(base),Decimal(0),floor if floor else None,ceiling,v[4])
    if component == Component.UNIFIED_SOCIAL_CONTRIBUTION and len(ordered)==1:
        e,c,r,v,p = ordered[0]
        if r and r.minimum_base_amount is not None and not v[4]:
            gross=Decimal(c.gross_amount)
            applies=(e.employment_kind=='primary' and Decimal(rate)==Decimal('.22') and gross>0)
            if applies and (e.start_date>period.start_date or (e.end_date and e.end_date<period.end_date)):
                raise Error('Partial-month primary employment requires dated USC minimum eligibility')
            if e.employment_kind is None and gross>0 and Decimal(rate)==Decimal('.22'):
                raise Error('Classify primary employment before applying the USC minimum')
            base=max(gross,Decimal(r.minimum_base_amount)) if applies else gross
            if r.maximum_base_amount is not None:
                base=min(base,Decimal(r.maximum_base_amount))
            values_by_contract[e.id]=(round_money(base),Decimal(0),r.minimum_base_amount if applies else None,r.maximum_base_amount,False)
    cumulative_base=Decimal(0);previous_amount=Decimal(0);selected=None
    for employment,source,base_rule,values,tax_profile in ordered:
        values=values_by_contract[employment.id]
        base=values[0]
        cumulative_base+=base
        cumulative_amount=round_money(cumulative_base*Decimal(rate))
        amount=cumulative_amount-previous_amount;previous_amount=cumulative_amount
        if employment.id==contract.id:
            selected=(values,amount,metadata)
        from app.models.payroll_statutory import PayrollStatutoryResult, PayrollStatutoryResultLine
        saved=(await db.execute(select(PayrollStatutoryResultLine.base_amount,
            PayrollStatutoryResultLine.amount,PayrollStatutoryResultLine.rate).join(PayrollStatutoryResult,
            (PayrollStatutoryResult.id==PayrollStatutoryResultLine.payroll_statutory_result_id)&
            (PayrollStatutoryResult.company_id==company_id)).where(
                PayrollStatutoryResultLine.company_id==company_id,PayrollStatutoryResult.payroll_calculation_id==source.id,
                PayrollStatutoryResultLine.component==component.value))).first()
        if saved is not None and tuple(saved)!=(base,amount,Decimal(rate)):
            raise Error('Saved peer tax allocation differs; correct the employee month as a whole')
    return selected
