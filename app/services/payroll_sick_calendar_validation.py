"""Validate medical dates without altering original orders or recorded time."""
from datetime import timedelta
from sqlalchemy import select
from app.models.payroll_sick_leave import PayrollSickLeaveCalculation
from app.models.payroll_insurance_evidence import PayrollInsuranceEvidence
from app.models.leave_request import LeaveRequest
from app.models.time_attendance import AttendanceRecord
from app.services.payroll_sick_revision_service import current_sick_calculation_filter


async def validate_calendar(db, *, company_id, calculation, data):
    from app.services.payroll_attendance_correction_service import AttendanceCorrectionError, effective_attendance_changes
    change=data.sick_calendar
    source=await db.scalar(select(PayrollSickLeaveCalculation).where(
        PayrollSickLeaveCalculation.company_id==company_id,
        PayrollSickLeaveCalculation.id==change.original_sick_id,current_sick_calculation_filter()))
    if source is None or source.employment_contract_id!=calculation.employment_contract_id:
        raise AttendanceCorrectionError('Current sick certificate must belong to the corrected employment contract')
    evidence=await db.scalar(select(PayrollInsuranceEvidence).where(
        PayrollInsuranceEvidence.company_id==company_id,PayrollInsuranceEvidence.id==change.insurance_evidence_id))
    if evidence is None or evidence.status!='verified':
        raise AttendanceCorrectionError('Reviewed insurance evidence is required for corrected medical dates')
    if change.end_date>data.correction_date:
        raise AttendanceCorrectionError('Medical correction date precedes the certificate end')
    from app.services.payroll_leave_correction_sources import leave_read_view
    leaves=(await db.scalars(select(LeaveRequest).where(LeaveRequest.company_id==company_id,
        LeaveRequest.employment_contract_id==source.employment_contract_id,
        LeaveRequest.status=='approved',LeaveRequest.id!=source.leave_request_id))).all()
    for leave in leaves:
        view=await leave_read_view(db,leave)
        dates=view.effective_leave_dates
        if dates is None:
            dates=[leave.start_date+timedelta(days=i) for i in range((leave.end_date-leave.start_date).days+1)]
        if any(change.start_date<=day<=change.end_date for day in dates):
            raise AttendanceCorrectionError('Corrected medical dates overlap another approved absence')
    current=await effective_attendance_changes(db,company_id=company_id,contract_id=source.employment_contract_id)
    requested={day.work_date:day.worked_minutes for day in data.days}
    rows=(await db.scalars(select(AttendanceRecord).where(AttendanceRecord.company_id==company_id,
        AttendanceRecord.employment_contract_id==source.employment_contract_id,
        AttendanceRecord.work_date>=change.start_date,AttendanceRecord.work_date<=change.end_date))).all()
    for row in rows:
        minutes=requested.get(row.work_date,current.get(row.work_date,{}).get('worked_minutes',row.worked_minutes))
        if minutes:
            raise AttendanceCorrectionError('Every worked date covered by the corrected certificate requires a zero-time correction')
    return source.leave_request_id
