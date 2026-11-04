"""Atomically apply independently reviewed severance corrections and carry payments forward."""
from datetime import date
from sqlalchemy import select
from app.models.payroll import PayrollCalculation, PayrollPeriod
from app.models.employment_contract import EmploymentContract
from app.models.payroll_sick_correction_application import PayrollSickCorrectionApplication as Application, PayrollSickPayrollRevision as Revision
from app.services.payroll_mutation_guard import serialized_payroll_mutation
from app.services.payroll_revision_service import current_calculation_filter,require_current_calculation
from app.services.payroll_severance_execution_review import require_execution_review


@serialized_payroll_mutation(ValueError)
async def execute_severance_correction(db, *, company_id, proposal_id, correction_date, actor_id):
    case,proposal,review=await require_execution_review(db,company_id=company_id,proposal_id=proposal_id,actor_id=actor_id)
    if type(correction_date) is not date or correction_date<max(review.review_date,case.as_of_date):
        raise ValueError('Correction date must follow the reviewed financial evidence')
    key=f'severance-proposal:{proposal.id}'
    existing=await db.scalar(select(Application).where(Application.company_id==company_id,Application.request_key==key))
    if existing is not None:
        if existing.correction_date!=correction_date:raise ValueError('Applied severance correction date differs')
        return existing
    original=await db.scalar(select(PayrollCalculation).where(PayrollCalculation.company_id==company_id,
        PayrollCalculation.id==case.original_payroll_calculation_id))
    if original is None:raise ValueError('Original severance payroll not found')
    await require_current_calculation(db,calculation=original,error_type=ValueError)
    period=await db.get(PayrollPeriod,original.payroll_period_id)
    if correction_date<period.end_date:raise ValueError('Correction cannot precede the payroll period end')
    contract=await db.get(EmploymentContract,original.employment_contract_id)
    affected=list((await db.scalars(select(PayrollCalculation).join(EmploymentContract,
        (EmploymentContract.company_id==PayrollCalculation.company_id)&
        (EmploymentContract.id==PayrollCalculation.employment_contract_id)).where(
        PayrollCalculation.company_id==company_id,PayrollCalculation.payroll_period_id==period.id,
        EmploymentContract.employee_id==contract.employee_id,current_calculation_filter()).order_by(PayrollCalculation.id))).all())
    from app.services.accounting_period_service import ensure_period_open
    await ensure_period_open(company_id,correction_date,db)
    from app.services.payroll_severance_calculated_financial import load_calculated_severance_financial
    from app.services.payroll_severance_trusted_classification import TRUSTED_POLICIES
    from app.services.payroll_severance_financial_approval_gate import snapshot_from_json
    from app.models.payroll_termination_severance import PayrollTerminationSeverance
    old_source=await db.get(PayrollTerminationSeverance,case.original_severance_source_id)
    before_severance=await load_calculated_severance_financial(db,company_id=company_id,
        calculation_id=original.id,income_date=old_source.termination_date,policies=TRUSTED_POLICIES)
    if before_severance.validate()!=snapshot_from_json(proposal.original_financial).validate():
        raise ValueError('Reviewed original financial snapshot differs from posted payroll')
    from app.services.payroll_sick_financial_sources import load_financial_snapshot
    from app.services.payroll_statutory_service import calculate_payroll_statutory_result
    from app.services.payroll_accounting_service import get_payroll_accounting_journal
    from app.services.payroll_sick_payroll_revision_service import build_sick_payroll_revision
    from app.services.payroll_sick_correction_journal import build_sick_difference_journal
    from app.services.payroll_sick_correction_application_service import snapshot_json
    from app.services.accounting_posting import post_journal_entry
    async with db.begin_nested():
        before={}
        for calc in affected:
            await calculate_payroll_statutory_result(db,company_id=company_id,payroll_calculation_id=calc.id,actor_user_id=actor_id)
            journal=await get_payroll_accounting_journal(db,company_id=company_id,payroll_calculation_id=calc.id)
            if journal is None or journal.status!='posted' or journal.entry_date>correction_date:
                raise ValueError('Postpayment correction requires active original accruals')
            before[calc.id]=await load_financial_snapshot(db,company_id=company_id,calculation_id=calc.id)
        from app.services.payroll_severance_verified_correction_service import create_verified_severance_correction,CorrectionEvidence
        new_source=await create_verified_severance_correction(db,company_id=company_id,
            employment_contract_id=original.employment_contract_id,payroll_input_id=original.payroll_input_id,
            previous_source_id=old_source.id,request_key=key,created_by=actor_id,
            evidence=CorrectionEvidence(reference=proposal.document_reference,effective_date=proposal.document_date),
            _financial_proposal_id=proposal.id)
        replacements=[]
        for calc in affected:
            replacement=await build_sick_payroll_revision(db,company_id=company_id,original=calc,
                original_sick=None,replacement_sick=None,source_pairs=[],request_key=f'{key}:{calc.id}',
                reason=proposal.document_reference,actor_id=actor_id,
                severance_replacement=(old_source,new_source) if calc.id==original.id else None)
            replacements.append((calc,replacement))
        for _,replacement in replacements:
            await calculate_payroll_statutory_result(db,company_id=company_id,payroll_calculation_id=replacement.id,actor_user_id=actor_id)
        from app.services.payroll_severance_corrected_tax_evidence import verify_corrected_severance_tax_evidence
        primary=next(new for old,new in replacements if old.id==original.id)
        await verify_corrected_severance_tax_evidence(db,company_id=company_id,
            financial_proposal_id=proposal.id,corrected_calculation_id=primary.id)
        application=Application(company_id=company_id,source_kind='severance',source_replacements={},
            request_key=key,request_fingerprint=proposal.request_fingerprint,reason=proposal.document_reference,
            correction_date=correction_date,created_by=actor_id,
            payroll_calculation_ids=[new.id for _,new in replacements])
        db.add(application);await db.flush()
        for old,new in replacements:
            after=await load_financial_snapshot(db,company_id=company_id,calculation_id=new.id)
            from app.models.payroll_severance_adjustment_journal import PayrollSeveranceAdjustmentJournal
            from app.models.journal_entry import JournalEntry
            from sqlalchemy.orm import selectinload
            draft_link=await db.scalar(select(PayrollSeveranceAdjustmentJournal).where(
                PayrollSeveranceAdjustmentJournal.company_id==company_id,
                PayrollSeveranceAdjustmentJournal.financial_proposal_id==proposal.id)) if old.id==original.id else None
            journal=await build_sick_difference_journal(db,company_id=company_id,replacement_calculation_id=new.id,
                original=before[old.id],replacement=after,correction_date=correction_date,actor_id=actor_id,
                source_kind='severance',persist=draft_link is None)
            if draft_link is not None:
                draft=await db.scalar(select(JournalEntry).options(selectinload(JournalEntry.lines)).where(
                    JournalEntry.company_id==company_id,JournalEntry.id==draft_link.journal_entry_id))
                def sides(row):return sorted((line.account_id,line.debit,line.credit) for line in row.lines)
                if (draft is None or journal is None or draft.status!='draft' or draft.entry_date!=correction_date
                        or draft.payroll_calculation_id is not None or sides(draft)!=sides(journal)):
                    raise ValueError('Prepared adjustment journal differs from the verified payroll difference')
                draft.payroll_calculation_id=new.id
                journal=draft
                await db.flush()
            db.add(Revision(company_id=company_id,application_id=application.id,original_calculation_id=old.id,
                replacement_calculation_id=new.id,journal_entry_id=journal.id if journal else None,
                original_financial=snapshot_json(before[old.id]),replacement_financial=snapshot_json(after),
                net_delta=after.net-before[old.id].net))
            await db.flush()
            if journal is not None:await post_journal_entry(db,company_id,journal.id)
        return application
