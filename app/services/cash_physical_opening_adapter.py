"""Read independently attested per-desk cash opening.

This adapter provides a reviewed calculation baseline.
It neither certifies external documents nor authorizes spending.

The caller owns the transaction and must hold the desk lock
through the complete movement validation and posting.
"""

from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cash_desk import CashDesk
from app.models.payroll_disbursement import (
    CashDeskOpeningEvidence,
    CashDeskOpeningReview,
)
from app.services.cash_physical_chronology_service import (
    CashOpening,
)


class CashOpeningUnavailableError(ValueError):
    pass


async def load_reviewed_cash_opening(
    db: AsyncSession,
    *,
    company_id: int,
    cash_desk_id: int,
    as_of_date: date,
    lock_cash_desk: bool = False,
) -> CashOpening:
    """Return a baseline only after independent review exists."""

    if (
        type(company_id) is not int
        or type(cash_desk_id) is not int
        or company_id <= 0
        or cash_desk_id <= 0
        or type(as_of_date) is not date
        or type(lock_cash_desk) is not bool
    ):
        raise CashOpeningUnavailableError(
            "Invalid company, desk, date or lock flag"
        )

    desk_statement = select(CashDesk).where(
        CashDesk.company_id == company_id,
        CashDesk.id == cash_desk_id,
    )

    if lock_cash_desk:
        desk_statement = desk_statement.with_for_update()

    desk = await db.scalar(desk_statement)

    if desk is None:
        raise CashOpeningUnavailableError(
            "Cash desk does not belong to this company"
        )

    row = await db.execute(
        select(
            CashDeskOpeningEvidence,
            CashDeskOpeningReview,
        )
        .join(
            CashDeskOpeningReview,
            (
                CashDeskOpeningReview.company_id
                == CashDeskOpeningEvidence.company_id
            )
            & (
                CashDeskOpeningReview.opening_evidence_id
                == CashDeskOpeningEvidence.id
            ),
        )
        .where(
            CashDeskOpeningEvidence.company_id == company_id,
            CashDeskOpeningEvidence.cash_desk_id == cash_desk_id,
        )
    )

    result = row.one_or_none()

    if result is None:
        raise CashOpeningUnavailableError(
            "Independent cash opening review required"
        )

    evidence, review = result

    if evidence.effective_date > as_of_date:
        raise CashOpeningUnavailableError(
            "Cash opening effective date is in the future"
        )

    if (
        evidence.currency_code != desk.currency_code
        or evidence.attested_by == review.reviewed_by
        or not evidence.evidence_reference.strip()
        or not review.review_reference.strip()
    ):
        raise CashOpeningUnavailableError(
            "Reviewed opening provenance is invalid"
        )

    amount = Decimal(evidence.declared_amount)

    if (
        not amount.is_finite()
        or amount < 0
        or amount.as_tuple().exponent < -2
    ):
        raise CashOpeningUnavailableError(
            "Reviewed opening amount is invalid"
        )

    return CashOpening(
        company_id=company_id,
        cash_desk_id=cash_desk_id,
        currency_code=evidence.currency_code,
        effective_date=evidence.effective_date,
        amount=amount,
        evidence_id=evidence.id,
    )
