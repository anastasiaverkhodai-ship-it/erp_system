"""Record independent review without authorizing statutory posting."""

from datetime import date

from sqlalchemy import select

from app.models.company import Company
from app.models.user import User
from app.models.payroll_severance_financial_proposal import (
    PayrollSeveranceFinancialProposal,
)
from app.models.payroll_severance_independent_review import (
    PayrollSeveranceIndependentReview,
)
from app.services.idempotency_fingerprint_service import (
    generate_request_fingerprint,
)


class SeveranceIndependentReviewError(ValueError):
    pass


DECISIONS = frozenset({
    "reviewed_pending_statutory",
    "rejected",
})


def validate_independent_review(
    *,
    proposal,
    reviewer_id,
    review_reference,
    review_date,
    decision,
    request_key,
):
    if proposal is None or proposal.status != "review_required":
        raise SeveranceIndependentReviewError(
            "Active immutable proposal required"
        )

    if (
        type(reviewer_id) is not int
        or reviewer_id <= 0
        or reviewer_id == proposal.submitted_by
    ):
        raise SeveranceIndependentReviewError(
            "Reviewer must differ from proposal submitter"
        )

    if (
        type(review_date) is not date
        or review_date < proposal.document_date
    ):
        raise SeveranceIndependentReviewError(
            "Valid review date required"
        )

    if (
        not isinstance(review_reference, str)
        or not 10 <= len(review_reference.strip()) <= 500
        or not isinstance(request_key, str)
        or not 8 <= len(request_key.strip()) <= 120
        or decision not in DECISIONS
    ):
        raise SeveranceIndependentReviewError(
            "Valid review document, request and decision required"
        )

    return {
        "company_id": proposal.company_id,
        "financial_proposal_id": proposal.id,
        "reviewer_id": reviewer_id,
        "review_reference": review_reference.strip(),
        "review_date": review_date.isoformat(),
        "decision": decision,
    }


async def record_independent_severance_review(
    db,
    *,
    company_id,
    financial_proposal_id,
    reviewer_id,
    review_reference,
    review_date,
    decision,
    request_key,
):
    if (
        type(company_id) is not int
        or company_id <= 0
        or type(financial_proposal_id) is not int
        or financial_proposal_id <= 0
    ):
        raise SeveranceIndependentReviewError(
            "Valid company and proposal required"
        )

    owner = await db.scalar(
        select(Company.id)
        .where(Company.id == company_id)
        .with_for_update()
    )

    if owner is None:
        raise SeveranceIndependentReviewError(
            "Company not found"
        )

    proposal = await db.scalar(
        select(PayrollSeveranceFinancialProposal).where(
            PayrollSeveranceFinancialProposal.company_id
            == company_id,
            PayrollSeveranceFinancialProposal.id
            == financial_proposal_id,
        )
    )

    payload = validate_independent_review(
        proposal=proposal,
        reviewer_id=reviewer_id,
        review_reference=review_reference,
        review_date=review_date,
        decision=decision,
        request_key=request_key,
    )

    # The user must actually exist. A numerical reviewer ID
    # alone is not evidence of a real independent person.
    reviewer = await db.scalar(
        select(User.id).where(User.id == reviewer_id)
    )

    if reviewer is None:
        raise SeveranceIndependentReviewError(
            "Reviewer user does not exist"
        )

    key = request_key.strip()
    fingerprint = generate_request_fingerprint(payload)

    existing = await db.scalar(
        select(PayrollSeveranceIndependentReview).where(
            PayrollSeveranceIndependentReview.company_id
            == company_id,
            PayrollSeveranceIndependentReview.request_key
            == key,
        )
    )

    if existing is not None:
        if (
            existing.financial_proposal_id
            != financial_proposal_id
            or existing.request_fingerprint != fingerprint
        ):
            raise SeveranceIndependentReviewError(
                "Idempotency key has different review evidence"
            )
        return existing

    competing = await db.scalar(
        select(PayrollSeveranceIndependentReview.id).where(
            PayrollSeveranceIndependentReview.company_id
            == company_id,
            PayrollSeveranceIndependentReview.financial_proposal_id
            == financial_proposal_id,
        )
    )

    if competing is not None:
        raise SeveranceIndependentReviewError(
            "Independent review already recorded"
        )

    row = PayrollSeveranceIndependentReview(
        company_id=company_id,
        financial_proposal_id=financial_proposal_id,
        reviewer_id=reviewer_id,
        review_reference=payload["review_reference"],
        review_date=review_date,
        decision=decision,
        request_key=key,
        request_fingerprint=fingerprint,
    )

    db.add(row)
    await db.flush()
    return row
