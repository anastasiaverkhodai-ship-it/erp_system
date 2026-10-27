"""Apply a sick correction atomically, preserving historical payments.

Only this orchestration may commit the source/revision building blocks.
The caller owns the outer transaction; a failure rolls the entire application
back to its savepoint, including all source versions and accounting changes.
"""
from dataclasses import asdict
from datetime import date
from sqlalchemy import select
from app.models.payroll import PayrollCalculation, PayrollCalculationLine
from app.models.payroll_sick_leave import PayrollSickLeaveCalculation
from app.models.payroll_sick_correction_application import PayrollSickCorrectionApplication, PayrollSickPayrollRevision
from app.services.payroll_mutation_guard import serialized_payroll_mutation
from app.services.payroll_revision_service import current_calculation_filter
from app.services.payroll_sick_replacement_source_service import materialize_sick_replacement
from app.services.payroll_sick_payroll_revision_service import build_sick_payroll_revision
from app.services.payroll_sick_financial_sources import load_financial_snapshot
from app.services.payroll_sick_correction_journal import build_sick_difference_journal
from app.services.payroll_statutory_service import calculate_payroll_statutory_result
from app.services.payroll_accounting_service import get_payroll_accounting_journal, generate_and_post_payroll_journal_entry
from app.services.accounting_posting import post_journal_entry
from app.services.accounting_period_service import ensure_period_open
from app.services.idempotency_fingerprint_service import generate_request_fingerprint


class SickCorrectionApplicationError(ValueError):
    pass


def snapshot_json(snapshot):
    return {name: str(value) for name, value in asdict(snapshot).items()}


@serialized_payroll_mutation(SickCorrectionApplicationError)
async def apply_sick_correction(db, *, company_id, original_sick_id, insurance_evidence_id,
                                correction_date, request_key, reason, actor_id):
    if (not isinstance(request_key, str) or not request_key.strip() or len(request_key) > 100
            or not isinstance(reason, str) or len(reason.strip()) < 10
            or not isinstance(correction_date, date)):
        raise SickCorrectionApplicationError('Valid request key, documented reason and correction date are required')
    payload = dict(original_sick_id=original_sick_id, insurance_evidence_id=insurance_evidence_id,
        correction_date=correction_date.isoformat(), reason=reason.strip())
    fingerprint = generate_request_fingerprint(payload)
    existing = await db.scalar(select(PayrollSickCorrectionApplication).where(
        PayrollSickCorrectionApplication.company_id == company_id,
        PayrollSickCorrectionApplication.request_key == request_key))
    if existing is not None:
        if existing.request_fingerprint != fingerprint:
            raise SickCorrectionApplicationError('Request key was used for a different sick correction')
        return existing
    original_sick = await db.scalar(select(PayrollSickLeaveCalculation).where(
        PayrollSickLeaveCalculation.company_id == company_id,
        PayrollSickLeaveCalculation.id == original_sick_id).with_for_update())
    if original_sick is None:
        raise SickCorrectionApplicationError('Original sick calculation not found')
    from app.services.payroll_sick_correction_scope import correction_source_scope
    from app.models.employment_contract import EmploymentContract
    employee_id, corrected_case_start, sources = await correction_source_scope(db,
        company_id=company_id, original=original_sick)
    source_ids = tuple(row[0].id for row in sources)
    period_ids = select(PayrollCalculation.payroll_period_id).join(PayrollCalculationLine,
        (PayrollCalculationLine.company_id==PayrollCalculation.company_id) &
        (PayrollCalculationLine.payroll_calculation_id==PayrollCalculation.id)).where(
        PayrollCalculation.company_id==company_id, current_calculation_filter(),
        PayrollCalculationLine.source_sick_leave_calculation_id.in_(source_ids))
    affected = list((await db.scalars(select(PayrollCalculation).join(EmploymentContract,
        (EmploymentContract.company_id==PayrollCalculation.company_id) &
        (EmploymentContract.id==PayrollCalculation.employment_contract_id)).where(
        PayrollCalculation.company_id==company_id, current_calculation_filter(),
        EmploymentContract.employee_id==employee_id, PayrollCalculation.payroll_period_id.in_(period_ids))
        .order_by(PayrollCalculation.payroll_period_id, PayrollCalculation.id))).all())
    if not affected:
        raise SickCorrectionApplicationError('This financial correction requires an existing payroll calculation')
    from app.models.payroll import PayrollPeriod
    latest_period_end = await db.scalar(select(PayrollPeriod.end_date).where(
        PayrollPeriod.company_id==company_id,
        PayrollPeriod.id.in_([row.payroll_period_id for row in affected]))
        .order_by(PayrollPeriod.end_date.desc()).limit(1))
    if latest_period_end is None or correction_date < latest_period_end:
        raise SickCorrectionApplicationError('Correction date must not precede the affected payroll period end')
    from app.models.payroll_deduction_result import PayrollDeductionResult
    from app.services.payroll_deduction_result_service import require_authorized_deduction_result
    deductions = await db.scalars(select(PayrollDeductionResult).where(
        PayrollDeductionResult.company_id==company_id,
        PayrollDeductionResult.payroll_calculation_id.in_([row.id for row in affected])))
    for deduction in deductions:
        require_authorized_deduction_result(deduction)
    from app.models.payroll_sick_correction import PayrollSickCorrection
    pending_requests = list((await db.scalars(select(PayrollSickCorrection).where(
        PayrollSickCorrection.company_id == company_id,
        PayrollSickCorrection.original_calculation_id.in_(source_ids)))).all())
    expected_evidence = {source.id: insurance_evidence_id if evidence.case_start==corrected_case_start else evidence.id
                         for source, _, evidence in sources}
    for pending in pending_requests:
        if pending.status != 'pending_review' or pending.insurance_evidence_id != expected_evidence[pending.original_calculation_id]:
            raise SickCorrectionApplicationError('Pending request uses different insurance evidence or is already applied')
    await ensure_period_open(company_id, correction_date, db)
    async with db.begin_nested():
        old_snapshots = {}
        posted = {}
        for original in affected:
            await calculate_payroll_statutory_result(db, company_id=company_id,
                payroll_calculation_id=original.id, actor_user_id=actor_id)
            old_snapshots[original.id] = await load_financial_snapshot(db,
                company_id=company_id, calculation_id=original.id)
            journal = await get_payroll_accounting_journal(db, company_id=company_id,
                payroll_calculation_id=original.id)
            if journal is not None and (journal.status != 'posted' or journal.entry_date > correction_date):
                raise SickCorrectionApplicationError('Original accrual must be posted no later than the correction date')
            posted[original.id] = journal is not None
        source_pairs = []
        for source, leave, evidence in sources:
            replacement = await materialize_sick_replacement(db, company_id=company_id,
                original_calculation_id=source.id,
                insurance_evidence_id=insurance_evidence_id if evidence.case_start==corrected_case_start else evidence.id,
                calculated_by=actor_id, excluded_source_ids=source_ids)
            source_pairs.append((source,replacement))
        replacement_sick = next(new for old,new in source_pairs if old.id==original_sick.id)
        replacements = []
        for original in affected:
            replacement = await build_sick_payroll_revision(db, company_id=company_id, original=original,
                original_sick=original_sick, replacement_sick=replacement_sick, source_pairs=source_pairs,
                request_key=f'sick:{fingerprint[:32]}:{original.id}', reason=reason.strip(), actor_id=actor_id)
            replacements.append((original, replacement))
        application = PayrollSickCorrectionApplication(company_id=company_id,
            original_sick_id=original_sick.id, replacement_sick_id=replacement_sick.id,
            request_key=request_key, request_fingerprint=fingerprint, reason=reason.strip(),
            correction_date=correction_date, created_by=actor_id,
            payroll_calculation_ids=[new.id for old,new in replacements],
            source_replacements={str(old.id):new.id for old,new in source_pairs})
        db.add(application)
        await db.flush()
        for original, replacement in replacements:
            await calculate_payroll_statutory_result(db, company_id=company_id,
                payroll_calculation_id=replacement.id, actor_user_id=actor_id)
            snapshot = await load_financial_snapshot(db, company_id=company_id, calculation_id=replacement.id)
            if posted[original.id]:
                journal = await build_sick_difference_journal(db, company_id=company_id,
                    replacement_calculation_id=replacement.id, original=old_snapshots[original.id],
                    replacement=snapshot, correction_date=correction_date, actor_id=actor_id)
                db.add(PayrollSickPayrollRevision(company_id=company_id, application_id=application.id,
                    original_calculation_id=original.id, replacement_calculation_id=replacement.id,
                    journal_entry_id=journal.id if journal else None,
                    original_financial=snapshot_json(old_snapshots[original.id]),
                    replacement_financial=snapshot_json(snapshot),
                    net_delta=snapshot.net-old_snapshots[original.id].net))
                await db.flush()
                if journal is not None:
                    await post_journal_entry(db, company_id, journal.id)
            else:
                # No actual liability or payment existed to carry forward.
                # Ordinary full posting remains appropriate for this revision.
                await generate_and_post_payroll_journal_entry(db, company_id=company_id,
                    payroll_calculation_id=replacement.id, created_by=actor_id, posting_date=correction_date)
        for pending in pending_requests:
            pending.status = 'applied'
            await db.flush()
        return application


@serialized_payroll_mutation(SickCorrectionApplicationError)
async def apply_unlinked_sick_correction(db, *, company_id, correction_id, correction_date, reviewed_by):
    """Apply a reviewed source-only request before any payroll consumes its case."""
    from app.models.payroll_sick_correction import PayrollSickCorrection
    from app.models.payroll_insurance_evidence import PayrollInsuranceEvidence
    from app.services.payroll_sick_leave_service import PayrollSickLeaveConflictError
    from app.services.payroll_sick_correction_scope import correction_source_scope
    request = await db.scalar(select(PayrollSickCorrection).where(
        PayrollSickCorrection.company_id==company_id, PayrollSickCorrection.id==correction_id))
    if request is None:
        raise PayrollSickLeaveConflictError('Sick correction request not found')
    if reviewed_by==request.requested_by:
        raise PayrollSickLeaveConflictError('Requester cannot approve own correction')
    evidence = await db.scalar(select(PayrollInsuranceEvidence).where(
        PayrollInsuranceEvidence.company_id==company_id,
        PayrollInsuranceEvidence.id==request.insurance_evidence_id))
    if evidence is None or evidence.reviewed_by!=reviewed_by:
        raise PayrollSickLeaveConflictError('Independent insurance evidence reviewer required')
    existing = await db.scalar(select(PayrollSickCorrectionApplication).where(
        PayrollSickCorrectionApplication.company_id==company_id,
        PayrollSickCorrectionApplication.original_sick_id==request.original_calculation_id))
    if existing is not None:
        if existing.correction_date!=correction_date:
            raise PayrollSickLeaveConflictError('Applied correction date differs')
        return existing
    original = await db.scalar(select(PayrollSickLeaveCalculation).where(
        PayrollSickLeaveCalculation.company_id==company_id,
        PayrollSickLeaveCalculation.id==request.original_calculation_id).with_for_update())
    _,case_start,sources = await correction_source_scope(db,company_id=company_id,original=original)
    ids=tuple(source.id for source,_,_ in sources)
    pending_requests=list((await db.scalars(select(PayrollSickCorrection).where(
        PayrollSickCorrection.company_id==company_id,
        PayrollSickCorrection.original_calculation_id.in_(ids)))).all())
    expected_evidence={source.id: evidence.id if old.case_start==case_start else old.id
                       for source,_,old in sources}
    if any(row.status!='pending_review' or row.insurance_evidence_id!=expected_evidence[row.original_calculation_id]
           for row in pending_requests):
        raise PayrollSickLeaveConflictError('Conflicting correction request in the affected sick case')
    used=await db.scalar(select(PayrollCalculationLine.id).where(
        PayrollCalculationLine.company_id==company_id,
        PayrollCalculationLine.source_sick_leave_calculation_id.in_(ids)).limit(1))
    if used is not None:
        raise PayrollSickLeaveConflictError('Linked sick sources require the full financial correction application')
    if not isinstance(correction_date,date) or any(leave.end_date>correction_date for _,leave,_ in sources):
        raise PayrollSickLeaveConflictError('Correction date precedes the sick case end')
    async with db.begin_nested():
        replacements={}
        for source,_,old_evidence in sources:
            new=await materialize_sick_replacement(db,company_id=company_id,
                original_calculation_id=source.id,
                insurance_evidence_id=evidence.id if old_evidence.case_start==case_start else old_evidence.id,
                calculated_by=reviewed_by,excluded_source_ids=ids)
            replacements[str(source.id)]=new.id
        app=PayrollSickCorrectionApplication(company_id=company_id,original_sick_id=original.id,
            replacement_sick_id=replacements[str(original.id)],request_key=f'unlinked:{request.id}',
            request_fingerprint=generate_request_fingerprint(dict(request_id=request.id,
                correction_date=correction_date.isoformat(),reviewed_by=reviewed_by)),
            reason=request.reason,correction_date=correction_date,created_by=reviewed_by,
            payroll_calculation_ids=[],source_replacements=replacements)
        db.add(app)
        await db.flush()
        for pending in pending_requests:
            pending.status='applied'
        await db.flush()
        return app
