from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.journal_entry import JournalEntry
from app.services.purchase_value_correction_fifo_impact_reconciliation_service import (
    PurchaseValueCorrectionFifoImpactReconciliationResult,
    reconcile_purchase_value_correction_fifo_impacts_for_fulfillment_line,
)
from app.services.purchase_value_correction_fifo_journal_service import (
    PurchaseValueCorrectionFifoJournalError,
    generate_and_post_purchase_value_correction_fifo_journal_entry,
    reverse_purchase_value_correction_fifo_journal_entry,
)


class PurchaseValueCorrectionFifoLifecycleError(
    Exception
):
    """Base PVC FIFO accounting lifecycle error."""


def _positive_int(
    value,
    *,
    field: str,
) -> int:
    if (
        not isinstance(
            value,
            int,
        )
        or isinstance(
            value,
            bool,
        )
        or value <= 0
    ):
        raise PurchaseValueCorrectionFifoLifecycleError(
            f"{field} must be a positive integer"
        )

    return value


async def post_created_purchase_value_correction_fifo_impact_journals(
    db: AsyncSession,
    *,
    result: PurchaseValueCorrectionFifoImpactReconciliationResult,
    created_by: int,
) -> tuple[
    JournalEntry,
    ...,
]:
    """
    Consume immutable FIFO impact events in exact persistence order.

    Original:
        generate/post one typed PVC FIFO JournalEntry.

    Reversal:
        reverse the original PVC FIFO JournalEntry and bind the
        reversal JournalEntry to the immutable reversal impact event.

    Replacement:
        persistence represents replacement as a new original event,
        so after its preceding reversal it is generated normally.

    Therefore an immutable topology correction is consumed as:

        original
            -> reversal
            -> replacement

    with no UPDATE of historical economic/accounting rows.

    Caller owns COMMIT / ROLLBACK.
    """

    _positive_int(
        created_by,
        field="created_by",
    )

    if not isinstance(
        result,
        PurchaseValueCorrectionFifoImpactReconciliationResult,
    ):
        raise PurchaseValueCorrectionFifoLifecycleError(
            "result must be "
            "PurchaseValueCorrectionFifoImpactReconciliationResult"
        )

    journals: list[
        JournalEntry
    ] = []

    try:
        for event in result.created_events:
            if event.reversal_of_id is None:
                journal = (
                    await generate_and_post_purchase_value_correction_fifo_journal_entry(
                        db,
                        event=event,
                        created_by=created_by,
                    )
                )

            else:
                journal = (
                    await reverse_purchase_value_correction_fifo_journal_entry(
                        db,
                        reversal_event=event,
                        reversed_by=created_by,
                    )
                )

            journals.append(
                journal
            )

    except PurchaseValueCorrectionFifoJournalError as exc:
        raise PurchaseValueCorrectionFifoLifecycleError(
            str(
                exc
            )
        ) from exc

    return tuple(
        journals
    )


async def reconcile_and_post_purchase_value_correction_fifo_impacts_for_fulfillment_line(
    db: AsyncSession,
    *,
    company_id: int,
    fulfillment_line_id: int,
    adjustment_date: date,
    created_by: int,
) -> PurchaseValueCorrectionFifoImpactReconciliationResult:
    """
    Atomic caller-owned orchestration for one physical receipt line:

        calculate desired FIFO value destinations
            -> immutable FIFO impact reconciliation
            -> consume created events in persistence order
            -> post/reverse typed JournalEntries

    A second reconciliation that creates no FIFO impact events creates
    no JournalEntries.

    This service never commits or rolls back.
    """

    _positive_int(
        company_id,
        field="company_id",
    )

    _positive_int(
        fulfillment_line_id,
        field="fulfillment_line_id",
    )

    _positive_int(
        created_by,
        field="created_by",
    )

    if not isinstance(
        adjustment_date,
        date,
    ):
        raise PurchaseValueCorrectionFifoLifecycleError(
            "adjustment_date must be a date"
        )

    result = (
        await reconcile_purchase_value_correction_fifo_impacts_for_fulfillment_line(
            db,
            company_id=company_id,
            fulfillment_line_id=fulfillment_line_id,
            adjustment_date=adjustment_date,
            created_by=created_by,
        )
    )

    await post_created_purchase_value_correction_fifo_impact_journals(
        db,
        result=result,
        created_by=created_by,
    )

    return result
