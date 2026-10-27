"""Independent human review of cash opening evidence.

Human attestation is not automated verification of a physical
inventory document. Caller owns transaction and rollback.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cash_desk import CashDesk
from app.models.payroll_disbursement import (
    CashDeskOpeningEvidence,
    CashDeskOpeningReview,
)


class CashOpeningReviewError(ValueError):
    pass


async def record_independent_cash_opening_review(
    db: AsyncSession,
    *,
    company_id: int,
    opening_evidence_id: int,
    reviewed_by: int,
    review_reference: str,
) -> CashDeskOpeningReview:
    for value in (company_id, opening_evidence_id, reviewed_by):
        if type(value) is not int or value <= 0:
            raise CashOpeningReviewError(
                "Positive integer identities required"
            )

    if not isinstance(review_reference, str):
        raise CashOpeningReviewError(
            "Review reference must be text"
        )

    reference = review_reference.strip()
    if not reference or len(reference) > 255:
        raise CashOpeningReviewError(
            "Review reference must contain 1-255 characters"
        )

    opening = await db.scalar(
        select(CashDeskOpeningEvidence)
        .where(
            CashDeskOpeningEvidence.company_id == company_id,
            CashDeskOpeningEvidence.id == opening_evidence_id,
        )
        .with_for_update()
    )
    if opening is None:
        raise CashOpeningReviewError(
            "Opening evidence does not belong to this company"
        )

    if opening.attested_by == reviewed_by:
        raise CashOpeningReviewError(
            "Self-review is not permitted"
        )

    desk = await db.scalar(
        select(CashDesk).where(
            CashDesk.company_id == company_id,
            CashDesk.id == opening.cash_desk_id,
        )
    )
    if desk is None or desk.currency_code != opening.currency_code:
        raise CashOpeningReviewError(
            "Cash desk identity or currency mismatch"
        )

    existing = await db.scalar(
        select(CashDeskOpeningReview).where(
            CashDeskOpeningReview.company_id == company_id,
            CashDeskOpeningReview.opening_evidence_id
            == opening_evidence_id,
        )
    )
    if existing is not None:
        if (
            existing.reviewed_by != reviewed_by
            or existing.review_reference != reference
        ):
            raise CashOpeningReviewError(
                "Conflicting independent review already exists"
            )
        return existing

    review = CashDeskOpeningReview(
        company_id=company_id,
        opening_evidence_id=opening_evidence_id,
        reviewed_by=reviewed_by,
        review_reference=reference,
    )
    db.add(review)
    await db.flush()
    return review
