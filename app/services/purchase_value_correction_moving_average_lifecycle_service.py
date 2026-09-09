from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.journal_entry import (
    JournalEntry,
)
from app.services.purchase_value_correction_moving_average_sales_return_reconciliation_service import (
    PurchaseValueCorrectionMovingAverageSalesReturnReconciliationResult,
)
from app.services.purchase_value_correction_moving_average_journal_service import (
    PurchaseValueCorrectionMovingAverageJournalError,
    generate_and_post_purchase_value_correction_moving_average_journal_entry,
    reverse_purchase_value_correction_moving_average_journal_entry,
)
from app.services.purchase_value_correction_moving_average_peer_reconciliation_service import (
    PurchaseValueCorrectionMovingAveragePeerReconciliationResult,
    reconcile_purchase_value_correction_moving_average_peers,
)
from app.services.purchase_value_correction_moving_average_replay_reconciliation_service import (
    PurchaseValueCorrectionMovingAverageReplayReconciliationResult,
    reconcile_purchase_value_correction_moving_average_replay,
)


class PurchaseValueCorrectionMovingAverageLifecycleError(
    Exception
):
    """Base PVC moving-average accounting lifecycle error."""


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
        raise PurchaseValueCorrectionMovingAverageLifecycleError(
            f"{field} must be a positive integer"
        )

    return value


async def _post_created_events(
    db: AsyncSession,
    *,
    created_events,
    created_by: int,
) -> tuple[
    JournalEntry,
    ...,
]:
    journals: list[
        JournalEntry
    ] = []

    try:
        for event in created_events:
            if event.reversal_of_id is None:
                journal = (
                    await generate_and_post_purchase_value_correction_moving_average_journal_entry(
                        db,
                        event=event,
                        created_by=created_by,
                    )
                )
            else:
                journal = (
                    await reverse_purchase_value_correction_moving_average_journal_entry(
                        db,
                        reversal_event=event,
                        reversed_by=created_by,
                    )
                )

            journals.append(
                journal
            )

    except PurchaseValueCorrectionMovingAverageJournalError as exc:
        raise PurchaseValueCorrectionMovingAverageLifecycleError(
            str(
                exc
            )
        ) from exc

    return tuple(
        journals
    )


async def post_created_purchase_value_correction_moving_average_replay_journals(
    db: AsyncSession,
    *,
    result: PurchaseValueCorrectionMovingAverageReplayReconciliationResult,
    created_by: int,
) -> tuple[
    JournalEntry,
    ...,
]:
    """
    Consume immutable single-allocation MA replay events in exact
    persistence order.

    original -> reversal -> replacement

    Caller owns COMMIT / ROLLBACK.
    """

    _positive_int(
        created_by,
        field="created_by",
    )

    if not isinstance(
        result,
        PurchaseValueCorrectionMovingAverageReplayReconciliationResult,
    ):
        raise PurchaseValueCorrectionMovingAverageLifecycleError(
            "result must be "
            "PurchaseValueCorrectionMovingAverageReplayReconciliationResult"
        )

    return await _post_created_events(
        db,
        created_events=result.created_events,
        created_by=created_by,
    )


async def post_created_purchase_value_correction_moving_average_sales_return_journals(
    db: AsyncSession,
    *,
    result: PurchaseValueCorrectionMovingAverageSalesReturnReconciliationResult,
    created_by: int,
) -> tuple[
    JournalEntry,
    ...,
]:
    """
    Consume immutable Sales Return PVC MA replay events
    in exact persistence order.

    Reconciliation already emits:
        reversal
        replacement

    This wrapper only posts typed PVC MA JournalEntries.
    Caller owns COMMIT / ROLLBACK.
    """

    _positive_int(
        created_by,
        field="created_by",
    )

    if not isinstance(
        result,
        PurchaseValueCorrectionMovingAverageSalesReturnReconciliationResult,
    ):
        raise PurchaseValueCorrectionMovingAverageLifecycleError(
            "result must be "
            "PurchaseValueCorrectionMovingAverageSalesReturnReconciliationResult"
        )

    return await _post_created_events(
        db,
        created_events=result.created_events,
        created_by=created_by,
    )


async def post_created_purchase_value_correction_moving_average_peer_journals(
    db: AsyncSession,
    *,
    result: PurchaseValueCorrectionMovingAveragePeerReconciliationResult,
    created_by: int,
) -> tuple[
    JournalEntry,
    ...,
]:
    """
    Consume peer-aware marginal MA replay events in exact persistence
    order.

    Multiple PVC allocations affecting one physical receipt are posted
    from their marginal replay attribution.

    Caller owns COMMIT / ROLLBACK.
    """

    _positive_int(
        created_by,
        field="created_by",
    )

    if not isinstance(
        result,
        PurchaseValueCorrectionMovingAveragePeerReconciliationResult,
    ):
        raise PurchaseValueCorrectionMovingAverageLifecycleError(
            "result must be "
            "PurchaseValueCorrectionMovingAveragePeerReconciliationResult"
        )

    return await _post_created_events(
        db,
        created_events=result.created_events,
        created_by=created_by,
    )


async def reconcile_and_post_purchase_value_correction_moving_average_replay(
    db: AsyncSession,
    *,
    company_id: int,
    allocation_event_id: int,
    adjustment_date: date,
    created_by: int,
) -> PurchaseValueCorrectionMovingAverageReplayReconciliationResult:
    """
    Single-allocation orchestration:

        replay
        -> immutable reconciliation
        -> typed JournalEntry lifecycle.

    Caller owns COMMIT / ROLLBACK.
    """

    _positive_int(
        company_id,
        field="company_id",
    )
    _positive_int(
        allocation_event_id,
        field="allocation_event_id",
    )
    _positive_int(
        created_by,
        field="created_by",
    )

    result = (
        await reconcile_purchase_value_correction_moving_average_replay(
            db,
            company_id=company_id,
            allocation_event_id=allocation_event_id,
            adjustment_date=adjustment_date,
            created_by=created_by,
        )
    )

    await post_created_purchase_value_correction_moving_average_replay_journals(
        db,
        result=result,
        created_by=created_by,
    )

    return result


async def reconcile_and_post_purchase_value_correction_moving_average_peers(
    db: AsyncSession,
    *,
    company_id: int,
    anchor_allocation_event_id: int,
    adjustment_date: date,
    created_by: int,
) -> PurchaseValueCorrectionMovingAveragePeerReconciliationResult:
    """
    Peer-aware orchestration for every active PVC allocation affecting
    one physical moving-average receipt:

        cumulative peer replay
        -> marginal attribution
        -> immutable reconciliation
        -> typed JournalEntry lifecycle.

    Caller owns COMMIT / ROLLBACK.
    """

    _positive_int(
        company_id,
        field="company_id",
    )
    _positive_int(
        anchor_allocation_event_id,
        field="anchor_allocation_event_id",
    )
    _positive_int(
        created_by,
        field="created_by",
    )

    result = (
        await reconcile_purchase_value_correction_moving_average_peers(
            db,
            company_id=company_id,
            anchor_allocation_event_id=anchor_allocation_event_id,
            adjustment_date=adjustment_date,
            created_by=created_by,
        )
    )

    await post_created_purchase_value_correction_moving_average_peer_journals(
        db,
        result=result,
        created_by=created_by,
    )

    return result
