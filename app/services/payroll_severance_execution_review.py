"""Resolve immutable independent approval for an atomic financial correction."""
from sqlalchemy import select
from app.models.payroll_severance_financial_proposal import PayrollSeveranceFinancialProposal as Proposal
from app.models.payroll_severance_correction_case import PayrollSeveranceCorrectionCase as Case
from app.models.payroll_severance_independent_review import PayrollSeveranceIndependentReview as Review


async def require_execution_review(db, *, company_id, proposal_id, actor_id):
    proposal=await db.scalar(select(Proposal).where(Proposal.company_id==company_id,Proposal.id==proposal_id))
    if proposal is None or proposal.status!='review_required':
        raise ValueError('Company-scoped severance proposal required')
    case=await db.scalar(select(Case).where(Case.company_id==company_id,Case.id==proposal.correction_case_id))
    review=await db.scalar(select(Review).where(Review.company_id==company_id,Review.financial_proposal_id==proposal.id))
    if (case is None or review is None or review.decision!='reviewed_pending_statutory'
            or review.reviewer_id!=actor_id or actor_id in (proposal.submitted_by,case.created_by)):
        raise ValueError('Independent approved severance review required')
    from app.services.payroll_severance_independent_review_service import require_severance_reviewer_permission
    await require_severance_reviewer_permission(db,company_id=company_id,reviewer_id=actor_id)
    return case,proposal,review
