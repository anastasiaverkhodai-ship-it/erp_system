"""Internal payroll revision builder for an atomic sick-correction application.

The caller owns the complete application transaction, including statutory
results, signed journal differences and financial carry-forward records.
This module is not exposed as an independently committable API.
"""
from sqlalchemy import select
from app.models.payroll import PayrollCalculation, PayrollCalculationLine, PayrollPeriod
from app.models.payroll_correction import PayrollCorrection
from app.models.leave_request import LeaveRequest
from app.services.payroll_revision_service import require_current_calculation
from app.services.payroll_sick_payroll_replay import replay_sick_payroll, SickPayrollReplayError


async def build_sick_payroll_revision(db, *, company_id, original, original_sick,
                                      replacement_sick, request_key, reason, actor_id, source_pairs=None):
    await require_current_calculation(db, calculation=original, error_type=SickPayrollReplayError)
    if original.company_id != company_id:
        raise SickPayrollReplayError('Payroll calculation belongs to another company')
    period = await db.scalar(select(PayrollPeriod).where(
        PayrollPeriod.company_id == company_id, PayrollPeriod.id == original.payroll_period_id))
    if period is None:
        raise SickPayrollReplayError('Sick payroll period not found')
    lines = list((await db.scalars(select(PayrollCalculationLine).where(
        PayrollCalculationLine.company_id == company_id,
        PayrollCalculationLine.payroll_calculation_id == original.id
    ).order_by(PayrollCalculationLine.line_no, PayrollCalculationLine.id))).all())
    from types import SimpleNamespace
    from app.services.payroll_correction_basis import verify_basis, replay_basis, SEAL
    # Production payroll appends independently classified severance after its
    # minimum-wage basis. Preserve that immutable source without pretending it
    # was part of the sick/minimum-wage computation.
    severance_lines = [line for line in lines if line.line_type == 'severance']
    working_lines = [line for line in lines if line.line_type != 'severance']
    working = original
    if severance_lines:
        from app.services.payroll_severance_trusted_classification import require_trusted_severance_snapshot
        await require_trusted_severance_snapshot(db, company_id=company_id, payroll_calculation_id=original.id)
        working = SimpleNamespace(**{name:getattr(original,name) for name in
            ('company_id','payroll_period_id','payroll_input_id','employment_contract_id','currency_code',
             'minimum_wage_assessment')},
            gross_amount=original.gross_amount-sum(line.amount for line in severance_lines))
    basis = verify_basis(working, working_lines)
    parent_fingerprint = basis['sha256']
    gross, derived, assessment = replay_basis(basis, original.minimum_wage_assessment.get('request'))
    assessment['correction_basis'] = basis
    pairs = source_pairs if source_pairs is not None else [(original_sick, replacement_sick)]
    for old_source, new_source in pairs:
        if not any(getattr(line,'source_sick_leave_calculation_id',None)==old_source.id for line in working_lines):
            continue
        leave = await db.scalar(select(LeaveRequest).where(
            LeaveRequest.company_id==company_id, LeaveRequest.id==old_source.leave_request_id))
        if leave is None:
            raise SickPayrollReplayError('Sick leave not found')
        gross, derived, assessment = replay_sick_payroll(calculation=working, lines=working_lines,
            original_sick=old_source, replacement_sick=new_source, leave=leave, period=period)
        # Internal replay of additional affected certificates uses the preceding
        # verified result; only the database may seal the persisted final row.
        assessment['correction_basis']['seal'] = SEAL
        working = SimpleNamespace(**{name:getattr(original,name) for name in
            ('company_id','payroll_period_id','payroll_input_id','employment_contract_id','currency_code')},
            gross_amount=gross, minimum_wage_assessment=assessment)
        working_lines = [SimpleNamespace(**line) for line in derived]
    assessment['correction_basis'].pop('seal',None)
    assessment['correction_basis']['parent_source_fingerprint'] = parent_fingerprint
    for source_line in severance_lines:
        values = {column.name:getattr(source_line,column.name)
                  for column in PayrollCalculationLine.__table__.columns
                  if column.name not in {'id','company_id','payroll_calculation_id','created_at'}}
        values['line_no'] = len(derived)+1
        derived.append(values)
        gross += source_line.amount
    replacement = PayrollCalculation(company_id=company_id,
        payroll_period_id=original.payroll_period_id, payroll_input_id=original.payroll_input_id,
        employment_contract_id=original.employment_contract_id, revision=original.revision + 1,
        currency_code=original.currency_code, gross_amount=gross, calculated_by=actor_id,
        minimum_wage_assessment=assessment)
    db.add(replacement)
    await db.flush()
    db.add_all([PayrollCalculationLine(company_id=company_id,
        payroll_calculation_id=replacement.id, **line) for line in derived])
    await db.flush()
    # The dedicated application carries old payments forward. The ordinary
    # correction service intentionally retains its reverse-before-link policy.
    db.add(PayrollCorrection(company_id=company_id,
        original_payroll_calculation_id=original.id,
        replacement_payroll_calculation_id=replacement.id,
        request_key=request_key, reason=reason, created_by=actor_id))
    await db.flush()
    await db.refresh(replacement)
    if severance_lines:
        from app.services.payroll_severance_trusted_classification import persist_trusted_severance_snapshot
        await persist_trusted_severance_snapshot(db, company_id=company_id, payroll_calculation_id=replacement.id)
    return replacement
