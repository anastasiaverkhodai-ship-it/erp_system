"""Employee-wide dated taxation of independently dated income sources.

Monthly benefits and USC limits are consumed once across all contracts. Source
amounts must already be attributable to policy windows; no calendar proration.
"""
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal as D, ROUND_HALF_UP
from sqlalchemy import select, or_
from app.models.employment_contract import EmploymentContract
from app.models.payroll import PayrollCalculation, PayrollCalculationLine, PayrollPeriod
from app.models.payroll_statutory import (PayrollStatutoryComponent as Component, PayrollStatutoryResult as Result,
    PayrollStatutoryResultLine as ResultLine, PayrollStatutoryLineSource as Source, PayrollDatedStatutorySeal as Seal)
from app.services.payroll_revision_service import current_calculation_filter, require_current_calculation


def money(value):return D(value).quantize(D('.01'),rounding=ROUND_HALF_UP)


async def dated_employee_plan(db, *, company_id, calculation):
    from app.services.payroll_statutory_service import (PayrollStatutoryValidationError as Error,
        resolve_statutory_rate,resolve_payroll_employee_tax_profile,resolve_payroll_statutory_base_rule)
    period=await db.get(PayrollPeriod,calculation.payroll_period_id)
    contract=await db.get(EmploymentContract,calculation.employment_contract_id)
    await require_current_calculation(db,calculation=calculation,error_type=Error)
    contracts=list((await db.scalars(select(EmploymentContract).where(EmploymentContract.company_id==company_id,
        EmploymentContract.employee_id==contract.employee_id,EmploymentContract.status!='cancelled',
        EmploymentContract.start_date<=period.end_date,
        or_(EmploymentContract.end_date.is_(None),EmploymentContract.end_date>=period.start_date))
        .order_by(EmploymentContract.id))).all())
    calculations=list((await db.scalars(select(PayrollCalculation).where(PayrollCalculation.company_id==company_id,
        PayrollCalculation.payroll_period_id==period.id,PayrollCalculation.employment_contract_id.in_([c.id for c in contracts]),
        current_calculation_filter()))).all())
    by_contract={c.employment_contract_id:c for c in calculations}
    if len(by_contract)!=len(contracts) or len(calculations)!=len(contracts):
        raise Error('Calculate all employee contracts before dated allocation')
    ordered=sorted(contracts,key=lambda c:(c.employment_kind!='primary',c.id))
    groups=[];ordinary_total=D(0);total_gross=D(0)
    for employment in ordered:
        calc=by_contract[employment.id]
        if calc.currency_code!='UAH':raise Error('Dated employee taxation requires UAH')
        lines=list((await db.scalars(select(PayrollCalculationLine).where(
            PayrollCalculationLine.company_id==company_id,PayrollCalculationLine.payroll_calculation_id==calc.id)
            .order_by(PayrollCalculationLine.line_no))).all())
        if sum((line.amount for line in lines),D(0))!=calc.gross_amount:
            raise Error('Verified dated allocation required: income sources do not reconcile to payroll gross')
        total_gross+=calc.gross_amount
        from app.services.payroll_severance_trusted_classification import require_trusted_severance_snapshot
        if any(line.line_type=='severance' for line in lines):
            await require_trusted_severance_snapshot(db,company_id=company_id,payroll_calculation_id=calc.id)
        for line in lines:
            if line.line_type=='salary':
                from app.services.payroll_dated_income_days import verify_dated_salary_line
                await verify_dated_salary_line(db,company_id=company_id,calculation=calc,line=line)
            if line.line_type!='severance':ordinary_total+=line.amount
            if line.amount<0:raise Error('Negative dated income requires a separate reviewed adjustment')
            for component in Component:
                pieces=await income_policy_pieces(db,company_id=company_id,period=period,
                    employment=employment,calc=calc,line=line,component=component)
                for part in pieces:
                    day=part['start']
                    rate=await resolve_statutory_rate(db,company_id=company_id,component=component,
                        employee_id=contract.employee_id,effective_date=day)
                    profile=await resolve_payroll_employee_tax_profile(db,company_id=company_id,
                        employee_id=contract.employee_id,employment_contract_id=employment.id,effective_date=day)
                    rule=await resolve_payroll_statutory_base_rule(db,company_id=company_id,component=component,
                        employment_kind=employment.employment_kind,tax_profile_category=profile.category if profile else None,
                        effective_date=day)
                    severance=line.line_type=='severance'
                    exempt=bool(profile and profile.category=='exempt' and rule and rule.exemption_applies)
                    if profile and profile.category=='exempt' and rule is None:
                        raise Error('Exemption requires an explicit component rule')
                    if severance:
                        expected=D('.18') if component==Component.PERSONAL_INCOME_TAX else D('.05')
                        if component!=Component.UNIFIED_SOCIAL_CONTRIBUTION and (exempt or rate.rate!=expected):
                            raise Error('Severance requires ordinary other-income taxation')
                    groups.append(dict(calc=calc,contract=employment,component=component,rate=rate,profile=profile,rule=rule,
                        amount=part['amount'],base=D(0) if exempt or (severance and component==Component.UNIFIED_SOCIAL_CONTRIBUTION) else part['amount'],
                        benefit=D(0),exempt=exempt,severance=severance,source=line,start=part['start'],end=part['end'],floor=None,ceiling=None))
    # PSP is a monthly entitlement, never a fresh deduction per dated segment.
    beneficiaries=[g for g in groups if g['component']==Component.PERSONAL_INCOME_TAX and not g['severance']
        and g['profile'] and g['profile'].category=='benefit_eligible' and g['rule'] and g['rule'].base_mode=='gross_after_benefit']
    if beneficiaries:
        parameters=set()
        for g in beneficiaries:
            p,r=g['profile'],g['rule']
            benefit=p.benefit_amount_override if p.benefit_amount_override is not None else r.benefit_amount
            limit=p.benefit_income_limit_override if p.benefit_income_limit_override is not None else r.benefit_income_limit
            if benefit is None or limit is None or limit<=0:raise Error('Monthly benefit requires confirmed amount and income threshold')
            parameters.add((D(benefit),D(limit)))
        if len(parameters)!=1:raise Error('Conflicting monthly benefit entitlements require reviewed classification')
        benefit,limit=next(iter(parameters));available=min(benefit,ordinary_total) if ordinary_total<=limit else D(0)
        for g in groups:
            if g['component']==Component.PERSONAL_INCOME_TAX and not g['severance'] and not g['exempt']:
                used=min(g['base'],available);g['base']-=used;g['benefit']=used;available-=used
                if used:
                    g['benefit_profile']=beneficiaries[0]['profile'];g['benefit_rule']=beneficiaries[0]['rule'];g['benefit_limit']=limit
    usc=[g for g in groups if g['component']==Component.UNIFIED_SOCIAL_CONTRIBUTION and not g['severance'] and not g['exempt']]
    maxima={D(g['rule'].maximum_base_amount) for g in usc if g['rule'] and g['rule'].maximum_base_amount is not None}
    if len(maxima)>1:raise Error('Conflicting employee monthly USC ceilings')
    ceiling=next(iter(maxima),None)
    from app.services.payroll_usc_eligibility_service import qualifies_for_monthly_usc_floor
    primary=[c for c in ordered if c.employment_kind=='primary']
    # No whole-month 22% minimum is manufactured for a month containing an
    # individual reduced rate. Keep the actual bases on both sides of its date.
    if len(primary)==1 and usc and all(g['rate'].rate==D('.22') for g in usc):
        floors={D(g['rule'].minimum_base_amount) for g in usc if g['contract'].id==primary[0].id
            and g['rule'] and g['rule'].minimum_base_amount is not None}
        if len(floors)>1:raise Error('Conflicting monthly USC floors')
        floor=next(iter(floors),D(0))
        if floor and await qualifies_for_monthly_usc_floor(db,company_id=company_id,period=period,contract=primary[0],calculation=by_contract[primary[0].id]):
            if ceiling is not None and floor>ceiling:raise Error('USC minimum exceeds maximum')
            total=sum((g['base'] for g in usc),D(0))
            target=next(g for g in usc if g['contract'].id==primary[0].id)
            if 0<total<floor:target['base']+=floor-total;target['floor']=floor
    remaining=ceiling
    for g in usc:
        if remaining is not None:
            g['base']=min(g['base'],max(D(0),remaining));remaining-=g['base'];g['ceiling']=ceiling
    # Group only identical tax policies, preserving all contributing source rows.
    merged={}
    for g in groups:
        key=(g['calc'].id,g['component'].value,g['rate'].id,
            g['profile'].id if g['profile'] else None,g['rule'].id if g['rule'] else None,
            g.get('benefit_profile').id if g.get('benefit_profile') else None,g['severance'])
        if key not in merged:merged[key]=dict(g,base=D(0),amount=D(0),benefit=D(0),sources=[])
        target=merged[key]
        for field in ('base','amount','benefit'):target[field]+=g[field]
        target['start']=min(target['start'],g['start']);target['end']=max(target['end'],g['end'])
        target['sources'].append((g['source'],g['amount'],g['start'],g['end']))
        if g['floor'] is not None:target['floor']=g['floor']
        if g['ceiling'] is not None:target['ceiling']=g['ceiling']
    cumulative=defaultdict(lambda:D(0));previous=defaultdict(lambda:D(0))
    results=defaultdict(list)
    for g in merged.values():
        key=(g['component'].value,g['rate'].rate)
        cumulative[key]+=g['base'];tax=money(cumulative[key]*g['rate'].rate)
        g['tax']=tax-previous[key];previous[key]=tax
        g['employee_gross']=total_gross
        results[g['calc'].id].append(g)
    return calculations,results


async def income_policy_pieces(db, *, company_id, period, employment, calc, line, component):
    """Authenticate policy on every day; never guess a split of an opaque sum."""
    from app.services.payroll_statutory_service import (PayrollStatutoryValidationError as Error,
        resolve_statutory_rate,resolve_payroll_employee_tax_profile,resolve_payroll_statutory_base_rule)
    start,end=line.source_effective_from,line.source_effective_to
    daily=None
    if start is None or end is None:
        from app.services.payroll_dated_income_days import income_days
        daily=await income_days(db,company_id=company_id,calculation=calc,line=line,period=period)
        if not daily:raise Error('Dated income dates are missing')
        start,end=min(daily),max(daily)
    if start>end or start<period.start_date or end>period.end_date:
        raise Error('Verified income dates fall outside payroll period')
    windows=[];last=None;day=start;window=start
    while day<=end:
        rate=await resolve_statutory_rate(db,company_id=company_id,component=component,employee_id=employment.employee_id,effective_date=day)
        profile=await resolve_payroll_employee_tax_profile(db,company_id=company_id,employee_id=employment.employee_id,
            employment_contract_id=employment.id,effective_date=day)
        rule=await resolve_payroll_statutory_base_rule(db,company_id=company_id,component=component,
            employment_kind=employment.employment_kind,tax_profile_category=profile.category if profile else None,effective_date=day)
        ids=(rate.id,profile.id if profile else None,rule.id if rule else None)
        if last is not None and ids!=last:windows.append((window,day-timedelta(days=1)));window=day
        last=ids;day+=timedelta(days=1)
    windows.append((window,end))
    if len(windows)==1:return [dict(start=start,end=end,amount=line.amount)]
    if daily is None:
        from app.services.payroll_dated_income_days import income_days
        daily=await income_days(db,company_id=company_id,calculation=calc,line=line,period=period)
    if sum(daily.values(),D(0))!=line.amount:
        raise Error('Verified daily income does not reconcile to source amount')
    return [dict(start=a,end=b,amount=sum((amount for day,amount in daily.items() if a<=day<=b),D(0)))
            for a,b in windows]


async def write_dated_employee_statutory_result(db, *, company_id, payroll_calculation_id, actor_user_id):
    from app.services.payroll_statutory_service import PayrollStatutoryValidationError as Error
    async with db.begin_nested():
        calc=await db.scalar(select(PayrollCalculation).where(PayrollCalculation.company_id==company_id,
            PayrollCalculation.id==payroll_calculation_id).with_for_update())
        if calc is None:raise Error('Payroll calculation not found in company')
        calculations,plans=await dated_employee_plan(db,company_id=company_id,calculation=calc)
        # Existing peer results must agree, so adding a new contract or changing
        # a policy cannot silently reallocate taxes already recorded elsewhere.
        for peer in calculations:
            old=await db.scalar(select(Result).where(Result.company_id==company_id,Result.payroll_calculation_id==peer.id))
            if old:
                saved=(await db.execute(select(ResultLine.component,ResultLine.source_rate_id,ResultLine.base_amount,ResultLine.amount)
                    .where(ResultLine.company_id==company_id,ResultLine.payroll_statutory_result_id==old.id))).all()
                expected=[(g['component'].value,g['rate'].id,money(g['base']),g['tax']) for g in plans[peer.id]]
                if sorted(map(tuple,saved))!=sorted(expected):raise Error('Saved peer taxes differ; correct the complete employee month')
        selected=plans[calc.id]
        withheld=sum((g['tax'] for g in selected if g['component']!=Component.UNIFIED_SOCIAL_CONTRIBUTION),D(0))
        employer=sum((g['tax'] for g in selected if g['component']==Component.UNIFIED_SOCIAL_CONTRIBUTION),D(0))
        if withheld>calc.gross_amount:raise Error('Withholdings exceed payroll gross')
        header=Result(company_id=company_id,payroll_calculation_id=calc.id,currency_code='UAH',gross_amount=calc.gross_amount,
            employee_withholding_amount=withheld,employer_contribution_amount=employer,
            net_amount=calc.gross_amount-withheld,calculated_by=actor_user_id)
        db.add(header);await db.flush()
        for index,g in enumerate(selected,1):
            rule=g.get('benefit_rule',g['rule']);profile=g.get('benefit_profile',g['profile'])
            row=ResultLine(company_id=company_id,payroll_statutory_result_id=header.id,line_no=index,
                component=g['component'].value,base_amount=money(g['base']),rate=g['rate'].rate,amount=g['tax'],currency_code='UAH',
                source_rate_id=g['rate'].id,source_tax_profile_id=profile.id if profile else None,
                source_base_rule_id=rule.id if rule else None,gross_base_amount=g['amount'],
                benefit_amount_applied=g['benefit'],minimum_base_amount_applied=g['floor'],maximum_base_amount_applied=g['ceiling'],
                exemption_applied=g['exempt'],base_rule_code=rule.rule_code if rule else None,
                base_rule_version=rule.rule_version if rule else None,rate_effective_from=g['start'],rate_effective_to=g['end'],
                employee_gross_amount=g['employee_gross'],benefit_income_limit_applied=g.get('benefit_limit'))
            db.add(row);await db.flush()
            sources={}
            for line,amount,start,end in g['sources']:
                item=sources.setdefault(line.id,dict(amount=D(0),start=start,end=end))
                item['amount']+=amount;item['start']=min(item['start'],start);item['end']=max(item['end'],end)
            for identifier,item in sources.items():
                db.add(Source(company_id=company_id,statutory_line_id=row.id,payroll_calculation_line_id=identifier,
                    source_amount=item['amount'],income_effective_from=item['start'],income_effective_to=item['end']))
        await db.flush();db.add(Seal(company_id=company_id,payroll_statutory_result_id=header.id));await db.flush()
        return header
