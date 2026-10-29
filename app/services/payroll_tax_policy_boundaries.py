"""Locate dated policy changes before immutable payroll sources are built."""
from datetime import timedelta
from sqlalchemy import select, or_
from app.models.employment_contract import EmploymentContract
from app.models.payroll_statutory import PayrollStatutoryRate
from app.models.payroll_tax import PayrollEmployeeTaxProfile, PayrollStatutoryBaseRule


async def tax_policy_boundaries(db, *, company_id, contract_id, start, end):
    contract=await db.scalar(select(EmploymentContract).where(
        EmploymentContract.company_id==company_id,EmploymentContract.id==contract_id))
    if contract is None:
        raise ValueError('Employment contract not found for dated tax sources')
    selectors=[(PayrollStatutoryRate,or_(PayrollStatutoryRate.employee_id.is_(None),
                                       PayrollStatutoryRate.employee_id==contract.employee_id)),
        (PayrollEmployeeTaxProfile,PayrollEmployeeTaxProfile.employment_contract_id==contract.id),
        (PayrollStatutoryBaseRule,or_(PayrollStatutoryBaseRule.employment_kind.is_(None),
                                    PayrollStatutoryBaseRule.employment_kind==contract.employment_kind))]
    boundaries=set()
    for model,selector in selectors:
        rows=(await db.scalars(select(model).where(model.company_id==company_id,selector,
            model.effective_from<=end,or_(model.effective_to.is_(None),model.effective_to>=start)))).all()
        for row in rows:
            if start<row.effective_from<=end:boundaries.add(row.effective_from)
            if row.effective_to is not None and start<=row.effective_to<end:
                boundaries.add(row.effective_to+timedelta(days=1))
    return sorted(boundaries)
