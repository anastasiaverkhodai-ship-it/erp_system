"""Find every certificate sharing the corrected case or monthly ceiling."""
from calendar import monthrange
from datetime import date
from sqlalchemy import select
from app.models.payroll_sick_leave import PayrollSickLeaveCalculation as Sick
from app.models.payroll_insurance_evidence import PayrollInsuranceEvidence as Evidence
from app.models.leave_request import LeaveRequest
from app.models.employment_contract import EmploymentContract
from app.services.payroll_sick_revision_service import current_sick_calculation_filter


async def correction_source_scope(db, *, company_id, original):
    evidence = await db.scalar(select(Evidence).where(Evidence.company_id==company_id,
        Evidence.id==original.insurance_evidence_id))
    if evidence is None:
        raise ValueError('Evidence-backed sick calculation required')
    rows = (await db.execute(select(Sick, LeaveRequest, Evidence)
        .join(LeaveRequest, (LeaveRequest.company_id==Sick.company_id) & (LeaveRequest.id==Sick.leave_request_id))
        .join(Evidence, (Evidence.company_id==Sick.company_id) & (Evidence.id==Sick.insurance_evidence_id))
        .join(EmploymentContract, (EmploymentContract.company_id==Sick.company_id) &
            (EmploymentContract.id==Sick.employment_contract_id))
        .where(Sick.company_id==company_id, EmploymentContract.employee_id==evidence.employee_id,
            current_sick_calculation_filter(), LeaveRequest.end_date>=evidence.case_start)
        .order_by(LeaveRequest.start_date, LeaveRequest.end_date, Sick.id))).all()
    case_rows = [row for row in rows if row[2].case_start==evidence.case_start]
    if not case_rows or original.id not in {row[0].id for row in case_rows}:
        raise ValueError('Correction requires a current sick source')
    def month_end(day):
        return date(day.year,day.month,monthrange(day.year,day.month)[1])
    frontier = month_end(max(row[1].end_date for row in case_rows))
    included = []
    for row in rows:
        if row[1].start_date > frontier:
            break
        included.append(row)
        frontier = max(frontier,month_end(row[1].end_date))
    return evidence.employee_id, evidence.case_start, included
