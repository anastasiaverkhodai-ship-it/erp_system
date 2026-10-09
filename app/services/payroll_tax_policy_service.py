"""Create dated policies before their affected monthly tax snapshots exist."""
from datetime import date
from sqlalchemy import select, or_
from app.models.payroll_tax import PayrollEmployeeTaxProfile, PayrollStatutoryBaseRule
from app.models.employment_contract import EmploymentContract
from app.models.payroll import PayrollCalculation, PayrollPeriod
from app.models.payroll_statutory import PayrollStatutoryResult
from app.services.payroll_mutation_guard import serialized_payroll_mutation
from app.services.payroll_statutory_service import PayrollStatutoryValidationError as Error


async def _unused_window(db,company_id,start,end,employee_id=None):
    query=select(PayrollStatutoryResult.id).join(PayrollCalculation,
        (PayrollCalculation.id==PayrollStatutoryResult.payroll_calculation_id)&(PayrollCalculation.company_id==company_id))\
        .join(PayrollPeriod,(PayrollPeriod.id==PayrollCalculation.payroll_period_id)&(PayrollPeriod.company_id==company_id))\
        .where(PayrollStatutoryResult.company_id==company_id,PayrollPeriod.start_date<=(end or date.max),PayrollPeriod.end_date>=start)
    if employee_id is not None:
        query=query.join(EmploymentContract,EmploymentContract.id==PayrollCalculation.employment_contract_id).where(EmploymentContract.employee_id==employee_id)
    if await db.scalar(query.limit(1)):
        raise Error('Tax policy affects a saved employee month; use a correction')


@serialized_payroll_mutation(Error)
async def create_tax_profile(db,*,company_id,data,created_by):
    contract=await db.scalar(select(EmploymentContract).where(EmploymentContract.company_id==company_id,
        EmploymentContract.id==data.employment_contract_id))
    if contract is None:
        raise Error('Employment contract not found in this company')
    rows=list((await db.scalars(select(PayrollEmployeeTaxProfile).where(
        PayrollEmployeeTaxProfile.company_id==company_id,PayrollEmployeeTaxProfile.employment_contract_id==contract.id,
        PayrollEmployeeTaxProfile.effective_from<=(data.effective_to or date.max),
        or_(PayrollEmployeeTaxProfile.effective_to.is_(None),PayrollEmployeeTaxProfile.effective_to>=data.effective_from)))).all())
    values=data.model_dump()
    if len(rows)==1 and all(getattr(rows[0],key)==value for key,value in values.items()):
        return rows[0]
    if rows:
        raise Error('Tax profile overlaps an existing profile')
    await _unused_window(db,company_id,data.effective_from,data.effective_to,contract.employee_id)
    row=PayrollEmployeeTaxProfile(company_id=company_id,employee_id=contract.employee_id,created_by=created_by,**values)
    db.add(row);await db.flush();return row


@serialized_payroll_mutation(Error)
async def create_tax_base_rule(db,*,company_id,data,created_by):
    rows=list((await db.scalars(select(PayrollStatutoryBaseRule).where(
        PayrollStatutoryBaseRule.company_id==company_id,PayrollStatutoryBaseRule.component==data.component,
        PayrollStatutoryBaseRule.employment_kind==data.employment_kind,
        PayrollStatutoryBaseRule.tax_profile_category==data.tax_profile_category,
        PayrollStatutoryBaseRule.effective_from<=(data.effective_to or date.max),
        or_(PayrollStatutoryBaseRule.effective_to.is_(None),PayrollStatutoryBaseRule.effective_to>=data.effective_from)))).all())
    values=data.model_dump()
    if len(rows)==1 and all(getattr(rows[0],key)==value for key,value in values.items()):
        return rows[0]
    if rows:
        raise Error('Tax base rule overlaps an existing selector')
    await _unused_window(db,company_id,data.effective_from,data.effective_to)
    row=PayrollStatutoryBaseRule(company_id=company_id,created_by=created_by,**values)
    db.add(row);await db.flush();return row


@serialized_payroll_mutation(Error)
async def end_tax_policy(db, *, company_id, policy_id, effective_to, profile):
    """End a policy prospectively without changing a consumed tax snapshot."""
    from datetime import timedelta

    model = PayrollEmployeeTaxProfile if profile else PayrollStatutoryBaseRule
    row = await db.scalar(select(model).where(
        model.company_id == company_id, model.id == policy_id,
    ).with_for_update())
    if row is None:
        raise Error('Tax policy not found in this company')
    if row.effective_to == effective_to:
        return row
    previous_end = row.effective_to or date.max
    if effective_to < row.effective_from or effective_to >= previous_end:
        raise Error('End date must shorten the policy within its existing date range')
    await _unused_window(db, company_id, effective_to + timedelta(days=1),
                         previous_end, row.employee_id if profile else None)
    row.effective_to = effective_to
    await db.flush()
    return row
