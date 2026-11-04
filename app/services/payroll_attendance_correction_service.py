"""Independent review and atomic salary settlement after attendance correction."""
from datetime import date
from sqlalchemy import select
from app.models.payroll import PayrollCalculation, PayrollCalculationLine, PayrollPeriod, PayrollInput, PayrollInputSalarySlice
from app.models.employment_contract import EmploymentContract
from app.models.time_attendance import AttendanceRecord
from app.models.payroll_attendance_correction import PayrollAttendanceCorrectionRequest as Request, PayrollAttendanceCorrection as Evidence
from app.models.payroll_sick_correction_application import PayrollSickCorrectionApplication as Application, PayrollSickPayrollRevision as Revision
from app.schemas.payroll_attendance_correction import AttendanceCorrectionCreate
from app.services.payroll_mutation_guard import serialized_payroll_mutation
from app.services.payroll_revision_service import require_current_calculation, current_calculation_filter
from app.services.idempotency_fingerprint_service import generate_request_fingerprint


class AttendanceCorrectionError(ValueError):
    pass


async def effective_attendance_changes(db, *, company_id, contract_id, as_of=None):
    query = (select(Request.payload, Application.id, Application.correction_date)
        .join(Evidence, (Evidence.company_id == Request.company_id) & (Evidence.request_id == Request.id))
        .join(Application, (Application.company_id == Evidence.company_id) & (Application.id == Evidence.application_id))
        .join(PayrollCalculation, (PayrollCalculation.company_id == Request.company_id) & (PayrollCalculation.id == Request.calculation_id))
        .where(Request.company_id == company_id, PayrollCalculation.employment_contract_id == contract_id)
        .order_by(Application.correction_date, Application.id))
    if as_of is not None:
        query = query.where(Application.correction_date <= as_of)
    result = {}
    for payload, app_id, corrected_on in (await db.execute(query)).all():
        for day in payload['days']:
            result[date.fromisoformat(day['work_date'])] = dict(day, application_id=app_id, correction_date=corrected_on)
    return result


async def prepare_attendance_correction(db, *, company_id, data, _calendar_leave_id=None, _single=False):
    calc = await db.scalar(select(PayrollCalculation).where(
        PayrollCalculation.company_id == company_id, PayrollCalculation.id == data.calculation_id))
    if calc is None:
        raise AttendanceCorrectionError('Payroll calculation not found in company')
    await require_current_calculation(db, calculation=calc, error_type=AttendanceCorrectionError)
    if data.sick_calendar is not None and not _single:
        from app.services.payroll_sick_calendar_validation import validate_calendar
        leave_id=await validate_calendar(db,company_id=company_id,calculation=calc,data=data)
        calculations=list((await db.scalars(select(PayrollCalculation).where(
            PayrollCalculation.company_id==company_id,
            PayrollCalculation.employment_contract_id==calc.employment_contract_id,
            current_calculation_filter()).order_by(PayrollCalculation.id))).all())
        grouped=[]; expectations=[]; covered=set()
        for target in calculations:
            target_period=await db.get(PayrollPeriod,target.payroll_period_id)
            days=[day for day in data.days if target_period.start_date<=day.work_date<=target_period.end_date]
            if not days and target.id!=calc.id:continue
            scoped=data.model_copy(update=dict(calculation_id=target.id,days=days))
            _,changes,expected=await prepare_attendance_correction(db,company_id=company_id,data=scoped,
                _calendar_leave_id=leave_id,_single=True)
            grouped.extend(dict(change,calculation_id=target.id,payroll_input_id=target.payroll_input_id) for change in changes)
            expectations.append(expected);covered.update(day.work_date for day in days)
        if covered!={day.work_date for day in data.days}:
            raise AttendanceCorrectionError('Every corrected date requires an existing current payroll month')
        return calc,grouped,dict(calculations=expectations)
    period = await db.get(PayrollPeriod, calc.payroll_period_id)
    if data.correction_date < period.end_date:
        raise AttendanceCorrectionError('Correction must not precede the payroll month end')
    current = await effective_attendance_changes(db, company_id=company_id, contract_id=calc.employment_contract_id)
    if any(day['correction_date'] > data.correction_date for day in current.values()):
        raise AttendanceCorrectionError('Correction cannot precede a reviewed attendance revision')
    from app.models.leave_request import LeaveRequest
    from app.services.payroll_leave_correction_sources import leave_read_view
    absences = list((await db.scalars(select(LeaveRequest).where(LeaveRequest.company_id == company_id,
        LeaveRequest.employment_contract_id == calc.employment_contract_id, LeaveRequest.status == 'approved',
        LeaveRequest.start_date <= data.correction_date))).all())
    calendar_leave_id = _calendar_leave_id
    if data.sick_calendar is not None and calendar_leave_id is None:
        from app.services.payroll_sick_calendar_validation import validate_calendar
        calendar_leave_id = await validate_calendar(db, company_id=company_id, calculation=calc, data=data)
    occupied = set()
    for leave in absences:
        view = await leave_read_view(db, leave)
        # The read projection returns the reviewed retained dates for corrected
        # leave; all dates of other approved absences remain protected.
        from datetime import timedelta
        dates = view.effective_leave_dates
        if leave.id == calendar_leave_id:
            dates = [data.sick_calendar.start_date + timedelta(days=i)
                for i in range((data.sick_calendar.end_date-data.sick_calendar.start_date).days+1)]
        if dates is None:
            dates = [leave.start_date + timedelta(days=i) for i in range((leave.end_date-leave.start_date).days+1)]
        occupied.update(date.fromisoformat(x) if isinstance(x,str) else x for x in dates)
    changes = []
    for day in sorted(data.days, key=lambda x:x.work_date):
        if not period.start_date <= day.work_date <= period.end_date:
            raise AttendanceCorrectionError('Attendance date outside payroll period')
        if day.worked_minutes and day.work_date in occupied:
            raise AttendanceCorrectionError('Correct the approved absence before recording work on its dates')
        row = await db.scalar(select(AttendanceRecord).where(AttendanceRecord.company_id == company_id,
            AttendanceRecord.employment_contract_id == calc.employment_contract_id,
            AttendanceRecord.work_date == day.work_date))
        if row is None:
            raise AttendanceCorrectionError('Finalized attendance source missing for correction date')
        old = current.get(day.work_date)
        changes.append(dict(work_date=day.work_date, attendance_id=row.id,
            old_minutes=old['worked_minutes'] if old else row.worked_minutes,
            new_minutes=day.worked_minutes))
    # Preview verifies the original creation-time basis and every salary slice.
    lines = list((await db.scalars(select(PayrollCalculationLine).where(
        PayrollCalculationLine.company_id == company_id, PayrollCalculationLine.payroll_calculation_id == calc.id
    ).order_by(PayrollCalculationLine.line_no))).all())
    source = await db.get(PayrollInput, calc.payroll_input_id)
    slices = list((await db.scalars(select(PayrollInputSalarySlice).where(
        PayrollInputSalarySlice.company_id == company_id, PayrollInputSalarySlice.payroll_input_id == source.id
    ).order_by(PayrollInputSalarySlice.effective_from))).all())
    from app.models.employee_salary_rate import EmployeeSalaryRate
    from app.services.payroll_correction_historical_salary_proof import verify_salary_rate_slice
    for part in slices:
        rate = await db.get(EmployeeSalaryRate, part.salary_rate_id)
        verify_salary_rate_slice(rate, part, company_id=company_id,
            employment_contract_id=calc.employment_contract_id)
    from app.services.payroll_attendance_premiums import prepare_time_premiums
    await prepare_time_premiums(db,company_id=company_id,calculation=calc,payroll_input=source,
        period=period,slices=slices,lines=lines,changes=changes,days=data.days)
    working = calc
    severance = [line for line in lines if line.line_type == 'severance']
    if severance:
        from types import SimpleNamespace
        from app.services.payroll_severance_trusted_classification import require_trusted_severance_snapshot
        await require_trusted_severance_snapshot(db, company_id=company_id, payroll_calculation_id=calc.id)
        working = SimpleNamespace(**{key:getattr(calc,key) for key in
            ('company_id','payroll_period_id','payroll_input_id','employment_contract_id','minimum_wage_assessment')},
            gross_amount=calc.gross_amount-sum(line.amount for line in severance))
        lines = [line for line in lines if line.line_type != 'severance']
    from app.services.payroll_attendance_payroll_replay import replay_attendance_payroll
    gross = working.gross_amount
    if changes:
        gross, _, _ = replay_attendance_payroll(calculation=working, lines=lines, slices=slices,
            monthly_norm_minutes=source.monthly_norm_minutes, changes=changes)
    expected = dict(calculation_id=calc.id, gross=str(gross),
        basis=calc.minimum_wage_assessment['correction_basis']['sha256'],
        changes=[dict(x,work_date=x['work_date'].isoformat()) for x in changes])
    return calc, changes, expected


@serialized_payroll_mutation(AttendanceCorrectionError)
async def request_attendance_correction(db, *, company_id, actor_id, data):
    payload = data.model_dump(mode='json')
    fingerprint = generate_request_fingerprint(payload)
    old = await db.scalar(select(Request).where(Request.company_id == company_id, Request.request_key == data.request_key))
    if old:
        if old.fingerprint != fingerprint:
            raise AttendanceCorrectionError('Request key contains different attendance correction')
        return old
    _, _, expected = await prepare_attendance_correction(db, company_id=company_id, data=data)
    row = Request(company_id=company_id, calculation_id=data.calculation_id, request_key=data.request_key,
        fingerprint=fingerprint, payload=dict(payload, expected=expected), created_by=actor_id)
    db.add(row); await db.flush()
    return row


@serialized_payroll_mutation(AttendanceCorrectionError)
async def apply_attendance_correction(db, *, company_id, request_id, actor_id):
    request = await db.scalar(select(Request).where(Request.company_id == company_id, Request.id == request_id))
    if request is None:
        raise AttendanceCorrectionError('Attendance correction request not found')
    if request.created_by == actor_id:
        raise AttendanceCorrectionError('Independent reviewer required')
    old = await db.scalar(select(Evidence).where(Evidence.company_id == company_id, Evidence.request_id == request.id))
    if old:
        return await db.get(Application, old.application_id)
    payload = dict(request.payload); expected = payload.pop('expected')
    data = AttendanceCorrectionCreate.model_validate(payload)
    original, changes, actual = await prepare_attendance_correction(db,company_id=company_id,data=data)
    if actual != expected:
        raise AttendanceCorrectionError('Attendance or payroll changed since the review request')
    from app.services.accounting_period_service import ensure_period_open
    await ensure_period_open(company_id,data.correction_date,db)
    contract = await db.get(EmploymentContract, original.employment_contract_id)
    affected = list((await db.scalars(select(PayrollCalculation).join(EmploymentContract,
        (EmploymentContract.company_id == PayrollCalculation.company_id) &
        (EmploymentContract.id == PayrollCalculation.employment_contract_id)).where(
        PayrollCalculation.company_id == company_id, PayrollCalculation.payroll_period_id == original.payroll_period_id,
        EmploymentContract.employee_id == contract.employee_id, current_calculation_filter())
        .order_by(PayrollCalculation.id))).all())
    from app.services.payroll_sick_correction_application_service import snapshot_json
    from app.services.payroll_sick_financial_sources import load_financial_snapshot
    from app.services.payroll_statutory_service import calculate_payroll_statutory_result
    from app.services.payroll_accounting_service import get_payroll_accounting_journal, generate_and_post_payroll_journal_entry
    from app.services.payroll_sick_payroll_revision_service import build_sick_payroll_revision
    from app.services.payroll_sick_correction_journal import build_sick_difference_journal
    from app.services.accounting_posting import post_journal_entry
    async with db.begin_nested():
        if data.sick_calendar is not None:
            from app.services.payroll_sick_correction_application_service import apply_sick_correction
            calendar=data.sick_calendar.model_dump(mode='json')
            from app.services.payroll_attendance_premiums import materialize_time_premiums
            by_calculation={}
            for change in changes:by_calculation.setdefault(change['calculation_id'],[]).append(change)
            for items in by_calculation.values():
                await materialize_time_premiums(db,company_id=company_id,payroll_input_id=items[0]['payroll_input_id'],
                    request=request,changes=items,actor_id=actor_id)
            medical_application=await apply_sick_correction(db,company_id=company_id,
                original_sick_id=calendar['original_sick_id'],insurance_evidence_id=calendar['insurance_evidence_id'],
                correction_date=data.correction_date,request_key=f'attendance-calendar:{request.id}',
                reason=data.reason,actor_id=actor_id,calendar_change=calendar,calendar_request_id=request.id,attendance_by_calculation=by_calculation)
            changes=[]
            original=await db.scalar(select(PayrollCalculation).where(
                PayrollCalculation.company_id==company_id,
                PayrollCalculation.payroll_input_id==original.payroll_input_id,current_calculation_filter()))
            affected=list((await db.scalars(select(PayrollCalculation).join(EmploymentContract,
                (EmploymentContract.company_id==PayrollCalculation.company_id)&
                (EmploymentContract.id==PayrollCalculation.employment_contract_id)).where(
                PayrollCalculation.company_id==company_id,PayrollCalculation.id.in_(medical_application.payroll_calculation_ids),
                EmploymentContract.employee_id==contract.employee_id,current_calculation_filter())
                .order_by(PayrollCalculation.id))).all())
        from app.services.payroll_attendance_premiums import materialize_time_premiums
        await materialize_time_premiums(db,company_id=company_id,payroll_input_id=original.payroll_input_id,
            request=request,changes=changes,actor_id=actor_id)
        before={}; posted={}; replacements=[]
        for calc in affected:
            await calculate_payroll_statutory_result(db,company_id=company_id,payroll_calculation_id=calc.id,actor_user_id=actor_id)
            before[calc.id]=await load_financial_snapshot(db,company_id=company_id,calculation_id=calc.id)
            journal=await get_payroll_accounting_journal(db,company_id=company_id,payroll_calculation_id=calc.id)
            if journal and (journal.status!='posted' or journal.entry_date>data.correction_date):
                raise AttendanceCorrectionError('Original accrual must be active before correction')
            posted[calc.id]=journal is not None
            new=await build_sick_payroll_revision(db,company_id=company_id,original=calc,
                original_sick=None,replacement_sick=None,source_pairs=[],
                attendance_changes=changes if calc.id==original.id else None,
                request_key=f'attendance:{request.id}:{calc.id}',reason=data.reason,actor_id=actor_id)
            replacements.append((calc,new))
        app=Application(company_id=company_id,source_kind='attendance',original_sick_id=None,replacement_sick_id=None,
            source_replacements={},payroll_calculation_ids=[new.id for _,new in replacements],
            request_key=f'attendance:{request.id}',request_fingerprint=request.fingerprint,
            reason=data.reason,correction_date=data.correction_date,created_by=request.created_by)
        db.add(app);await db.flush()
        db.add(Evidence(company_id=company_id,request_id=request.id,application_id=app.id,reviewed_by=actor_id))
        await db.flush()
        for calc,new in replacements:
            await calculate_payroll_statutory_result(db,company_id=company_id,payroll_calculation_id=new.id,actor_user_id=actor_id)
            after=await load_financial_snapshot(db,company_id=company_id,calculation_id=new.id)
            if posted[calc.id]:
                journal=await build_sick_difference_journal(db,company_id=company_id,replacement_calculation_id=new.id,
                    original=before[calc.id],replacement=after,correction_date=data.correction_date,actor_id=actor_id,source_kind='attendance')
                db.add(Revision(company_id=company_id,application_id=app.id,original_calculation_id=calc.id,
                    replacement_calculation_id=new.id,journal_entry_id=journal.id if journal else None,
                    original_financial=snapshot_json(before[calc.id]),replacement_financial=snapshot_json(after),
                    net_delta=after.net-before[calc.id].net))
                await db.flush()
                if journal:await post_journal_entry(db,company_id,journal.id)
            else:
                await generate_and_post_payroll_journal_entry(db,company_id=company_id,
                    payroll_calculation_id=new.id,created_by=actor_id,posting_date=data.correction_date)
        return app


async def attendance_read_views(db, rows, *, as_of=None):
    """Expose effective time alongside the unchanged original attendance."""
    from app.schemas.time_attendance import AttendanceRead
    projections = {}
    result = []
    for row in rows:
        key = (row.company_id, row.employment_contract_id)
        if key not in projections:
            projections[key] = await effective_attendance_changes(db,
                company_id=row.company_id, contract_id=row.employment_contract_id, as_of=as_of)
        change = projections[key].get(row.work_date)
        view = AttendanceRead.model_validate(row)
        view.effective_status = change['status'] if change else row.status
        view.effective_worked_minutes = change['worked_minutes'] if change else row.worked_minutes
        view.correction_application_id = change['application_id'] if change else None
        view.correction_date = change['correction_date'] if change else None
        result.append(view)
    return result
