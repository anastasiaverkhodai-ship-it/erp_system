"""Review exact earning dates before an immutable monthly tax assessment."""
from datetime import date
from decimal import Decimal
from sqlalchemy import select
from app.models.payroll import PayrollCalculation, PayrollCalculationLine, PayrollPeriod
from app.models.payroll_statutory import PayrollStatutoryResult
from app.models.employment_contract import EmploymentContract
from app.models.payroll_income_dates import PayrollIncomeDateRequest as Request, PayrollIncomeDateApproval as Approval
from app.schemas.payroll_income_dates import IncomeDateRequestCreate
from app.services.payroll_mutation_guard import serialized_payroll_mutation
from app.services.payroll_revision_service import require_current_calculation,current_calculation_filter
from app.services.idempotency_fingerprint_service import generate_request_fingerprint


async def validate_income_dates(db, *, company_id, data):
    line=await db.scalar(select(PayrollCalculationLine).where(
        PayrollCalculationLine.company_id==company_id,PayrollCalculationLine.id==data.line_id))
    if line is None or line.line_type!='manual_adjustment':
        raise ValueError('Company-scoped manual adjustment required')
    calc=await db.get(PayrollCalculation,line.payroll_calculation_id)
    await require_current_calculation(db,calculation=calc,error_type=ValueError)
    period=await db.get(PayrollPeriod,calc.payroll_period_id)
    contract=await db.get(EmploymentContract,calc.employment_contract_id)
    if sum((item.amount for item in data.dates),Decimal(0))!=line.amount:
        raise ValueError('Dated amounts must reconcile exactly to the adjustment')
    if any(not max(period.start_date,contract.start_date)<=item.income_date<=min(period.end_date,contract.end_date or period.end_date)
           or (item.amount*line.amount<0) for item in data.dates):
        raise ValueError('Income dates or amount signs differ from their source')
    existing=await db.scalar(select(PayrollStatutoryResult.id).join(PayrollCalculation,
        PayrollCalculation.id==PayrollStatutoryResult.payroll_calculation_id).join(EmploymentContract,
        EmploymentContract.id==PayrollCalculation.employment_contract_id).where(
        PayrollStatutoryResult.company_id==company_id,EmploymentContract.employee_id==contract.employee_id,
        PayrollCalculation.payroll_period_id==period.id,current_calculation_filter()).limit(1))
    if existing:
        raise ValueError('Employee month is already assessed; use a new payroll revision')
    return line


@serialized_payroll_mutation(ValueError)
async def request_income_dates(db, *, company_id, actor_id, data):
    payload=data.model_dump(mode='json');fingerprint=generate_request_fingerprint(payload)
    existing=await db.scalar(select(Request).where(Request.company_id==company_id,Request.request_key==data.request_key))
    if existing:
        if existing.fingerprint!=fingerprint:raise ValueError('Income-date key belongs to different evidence')
        return existing
    await validate_income_dates(db,company_id=company_id,data=data)
    row=Request(company_id=company_id,line_id=data.line_id,request_key=data.request_key,
        fingerprint=fingerprint,payload=payload,created_by=actor_id)
    db.add(row);await db.flush();return row


@serialized_payroll_mutation(ValueError)
async def approve_income_dates(db, *, company_id, request_id, actor_id):
    request=await db.scalar(select(Request).where(Request.company_id==company_id,Request.id==request_id))
    if request is None:raise ValueError('Income-date request not found')
    if request.created_by==actor_id:raise ValueError('Independent income-date reviewer required')
    existing=await db.scalar(select(Approval).where(Approval.company_id==company_id,Approval.line_id==request.line_id))
    if existing:
        if existing.request_id!=request.id:raise ValueError('This income line already has approved dates')
        return existing
    await validate_income_dates(db,company_id=company_id,data=IncomeDateRequestCreate.model_validate(request.payload))
    row=Approval(company_id=company_id,line_id=request.line_id,request_id=request.id,reviewed_by=actor_id)
    db.add(row);await db.flush();return row


async def approved_income_days(db, *, company_id, line):
    # Unchanged manual lines retain approved dates through immutable payroll
    # revisions. Changed amounts/descriptions cannot inherit an old approval.
    from sqlalchemy import text
    match=(await db.execute(text("""WITH RECURSIVE lineage AS (
        SELECT l.*, ARRAY[l.id] AS visited, 0 AS depth FROM payroll_calculation_lines l
            WHERE l.company_id=:company AND l.id=:line
        UNION ALL
        SELECT p.*, a.visited || p.id, a.depth+1 FROM lineage a
        JOIN payroll_corrections c ON c.company_id=a.company_id
            AND c.replacement_payroll_calculation_id=a.payroll_calculation_id
        JOIN payroll_calculation_lines p ON p.company_id=a.company_id
            AND p.payroll_calculation_id=c.original_payroll_calculation_id
            AND p.line_type=a.line_type AND p.amount=a.amount
                    AND (SELECT count(*) FROM payroll_calculation_lines sibling
                         WHERE sibling.company_id=a.company_id AND sibling.payroll_calculation_id=p.payroll_calculation_id
                           AND sibling.line_type='manual_adjustment')=1
                    AND (SELECT count(*) FROM payroll_calculation_lines sibling
                         WHERE sibling.company_id=a.company_id AND sibling.payroll_calculation_id=a.payroll_calculation_id
                           AND sibling.line_type='manual_adjustment')=1
            AND p.currency_code=a.currency_code AND p.quantity IS NOT DISTINCT FROM a.quantity
            AND p.rate IS NOT DISTINCT FROM a.rate AND p.description IS NOT DISTINCT FROM a.description
        WHERE NOT p.id=ANY(a.visited)
    ) SELECT r.payload, a.id FROM lineage a JOIN payroll_income_date_approvals v
        ON v.company_id=a.company_id AND v.line_id=a.id
        JOIN payroll_income_date_requests r ON r.company_id=v.company_id AND r.id=v.request_id
        ORDER BY a.depth LIMIT 1"""),dict(company=company_id,line=line.id))).first()
    payload,approved_line_id=match if match else (None,None)
    if payload is None:raise ValueError('Manual adjustment requires independently approved earning dates')
    data=IncomeDateRequestCreate.model_validate(payload)
    days={item.income_date:item.amount for item in data.dates}
    if sum(days.values(),Decimal(0))!=line.amount or data.line_id!=approved_line_id:
        raise ValueError('Approved income dates no longer match the frozen source')
    return days
