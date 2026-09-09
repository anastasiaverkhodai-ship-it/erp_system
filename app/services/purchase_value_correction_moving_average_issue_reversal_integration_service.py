from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.purchase_value_correction_moving_average_replay_event import (
    PurchaseValueCorrectionMovingAverageReplayEvent,
)
from app.services.purchase_value_correction_moving_average_peer_reconciliation_service import (
    PurchaseValueCorrectionMovingAveragePeerReconciliationResult,
    reconcile_purchase_value_correction_moving_average_peers,
)
from app.services.purchase_value_correction_moving_average_peer_source_loader import (
    load_purchase_value_correction_moving_average_peer_source,
)
from app.services.purchase_value_correction_moving_average_replay_persistence_service import (
    _active_originals,
    _validate_history,
)


class PurchaseValueCorrectionMovingAverageIssueReversalIntegrationError(
    Exception
):
    pass


class PurchaseValueCorrectionMovingAverageIssueReversalIntegrationIntegrityError(
    PurchaseValueCorrectionMovingAverageIssueReversalIntegrationError
):
    pass


class PurchaseValueCorrectionMovingAverageIssueReversalCrossReceiptError(
    PurchaseValueCorrectionMovingAverageIssueReversalIntegrationError
):
    pass


@dataclass(
    frozen=True,
    slots=True,
)
class PurchaseValueCorrectionMovingAverageIssueReversalIntegrationResult:
    company_id: int
    source_moving_average_movement_id: int
    active_allocation_event_ids: tuple[int, ...]
    reconciliation_result: (
        PurchaseValueCorrectionMovingAveragePeerReconciliationResult
        | None
    )

    @property
    def changed(self) -> bool:
        return (
            self.reconciliation_result is not None
            and bool(
                self.reconciliation_result.created_events
            )
        )


def _positive_id(
    value,
    *,
    field: str,
) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value <= 0
    ):
        raise (
            PurchaseValueCorrectionMovingAverageIssueReversalIntegrationIntegrityError(
                f"{field} must be a positive integer"
            )
        )

    return value



async def load_original_moving_average_issues_for_document(
    db: AsyncSession,
    *,
    company_id: int,
    document_id: int,
) -> tuple:
    """
    Load immutable original MA ISSUE movements for one warehouse
    document.

    Reversal rows are excluded. No writes / commit / rollback.
    """

    company_id = _positive_id(
        company_id,
        field="company_id",
    )

    document_id = _positive_id(
        document_id,
        field="document_id",
    )

    from app.models.moving_average_movement import (
        MovingAverageMovement,
    )
    from app.models.stock_ledger import (
        StockMovementType,
    )

    rows = (
        await db.execute(
            select(
                MovingAverageMovement
            )
            .where(
                MovingAverageMovement.company_id
                == company_id,
                MovingAverageMovement.document_id
                == document_id,
                MovingAverageMovement.movement_type
                == StockMovementType.ISSUE,
                MovingAverageMovement.reversal_of_id.is_(
                    None
                ),
            )
            .order_by(
                MovingAverageMovement.id
            )
        )
    ).scalars().all()

    return tuple(
        rows
    )


async def reconcile_purchase_value_correction_moving_average_after_issue_reversal(
    db: AsyncSession,
    *,
    company_id: int,
    source_moving_average_movement_id: int,
    reversal_date: date,
    created_by: int,
) -> PurchaseValueCorrectionMovingAverageIssueReversalIntegrationResult:
    """
    Reconcile PVC MA topology after one technical reversal of a
    historical normal MA ISSUE.

    Required caller order:

        1. reverse base MovingAverageMovement ISSUE
        2. reverse normal ISSUE accounting
        3. THIS SERVICE
        4. typed PVC MA JournalEntry lifecycle

    Expected topology transition:

        issued + remaining on_hand
            ->
        restored on_hand

    Existing peer replay rebuilds desired state from immutable MA
    original/reversal history. Historical MovingAverageMovement and
    InventoryCostEntry rows are never mutated.

    This service does not COMMIT or ROLLBACK.
    """

    company_id = _positive_id(
        company_id,
        field="company_id",
    )

    source_moving_average_movement_id = _positive_id(
        source_moving_average_movement_id,
        field="source_moving_average_movement_id",
    )

    created_by = _positive_id(
        created_by,
        field="created_by",
    )

    if not isinstance(
        reversal_date,
        date,
    ):
        raise (
            PurchaseValueCorrectionMovingAverageIssueReversalIntegrationIntegrityError(
                "reversal_date must be a date"
            )
        )

    history = tuple(
        (
            await db.execute(
                select(
                    PurchaseValueCorrectionMovingAverageReplayEvent
                )
                .where(
                    PurchaseValueCorrectionMovingAverageReplayEvent.company_id
                    == company_id,
                    PurchaseValueCorrectionMovingAverageReplayEvent.source_moving_average_movement_id
                    == source_moving_average_movement_id,
                )
                .order_by(
                    PurchaseValueCorrectionMovingAverageReplayEvent.id
                )
                .with_for_update()
            )
        ).scalars().all()
    )

    if not history:
        return (
            PurchaseValueCorrectionMovingAverageIssueReversalIntegrationResult(
                company_id=company_id,
                source_moving_average_movement_id=(
                    source_moving_average_movement_id
                ),
                active_allocation_event_ids=(),
                reconciliation_result=None,
            )
        )

    _validate_history(
        history
    )

    active = _active_originals(
        history
    )

    if not active:
        return (
            PurchaseValueCorrectionMovingAverageIssueReversalIntegrationResult(
                company_id=company_id,
                source_moving_average_movement_id=(
                    source_moving_average_movement_id
                ),
                active_allocation_event_ids=(),
                reconciliation_result=None,
            )
        )

    allocation_ids: list[int] = []

    for event in active:
        if event.effect_kind != "issued":
            raise (
                PurchaseValueCorrectionMovingAverageIssueReversalIntegrationIntegrityError(
                    "Active PVC event linked to reversed ISSUE "
                    "must have effect_kind='issued'"
                )
            )

        if (
            event.source_moving_average_movement_id
            != source_moving_average_movement_id
        ):
            raise (
                PurchaseValueCorrectionMovingAverageIssueReversalIntegrationIntegrityError(
                    "PVC issued provenance does not match "
                    "the reversed MA ISSUE"
                )
            )

        if event.source_inventory_cost_entry_id is None:
            raise (
                PurchaseValueCorrectionMovingAverageIssueReversalIntegrationIntegrityError(
                    "Active PVC issued event is missing "
                    "InventoryCostEntry provenance"
                )
            )

        allocation_ids.append(
            _positive_id(
                event.purchase_value_correction_allocation_event_id,
                field="allocation event id",
            )
        )

    unique_allocation_ids = tuple(
        sorted(
            set(
                allocation_ids
            )
        )
    )

    if not unique_allocation_ids:
        return (
            PurchaseValueCorrectionMovingAverageIssueReversalIntegrationResult(
                company_id=company_id,
                source_moving_average_movement_id=(
                    source_moving_average_movement_id
                ),
                active_allocation_event_ids=(),
                reconciliation_result=None,
            )
        )

    anchor_id = unique_allocation_ids[
        0
    ]

    peer_source = (
        await load_purchase_value_correction_moving_average_peer_source(
            db,
            company_id=company_id,
            allocation_event_id=anchor_id,
        )
    )

    peer_ids = set(
        peer_source.allocation_event_ids
    )

    active_ids = set(
        unique_allocation_ids
    )

    if not active_ids.issubset(
        peer_ids
    ):
        raise (
            PurchaseValueCorrectionMovingAverageIssueReversalCrossReceiptError(
                "Active PVC issued overlays for one reversed "
                "MA ISSUE span multiple physical receipt roots"
            )
        )

    reconciliation = (
        await reconcile_purchase_value_correction_moving_average_peers(
            db,
            company_id=company_id,
            anchor_allocation_event_id=anchor_id,
            adjustment_date=reversal_date,
            created_by=created_by,
        )
    )

    return (
        PurchaseValueCorrectionMovingAverageIssueReversalIntegrationResult(
            company_id=company_id,
            source_moving_average_movement_id=(
                source_moving_average_movement_id
            ),
            active_allocation_event_ids=(
                unique_allocation_ids
            ),
            reconciliation_result=(
                reconciliation
            ),
        )
    )
