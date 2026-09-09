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
    PurchaseValueCorrectionMovingAveragePeerSource,
    load_purchase_value_correction_moving_average_peer_source,
)
from app.services.purchase_value_correction_moving_average_replay_persistence_service import (
    _active_originals,
    _validate_history,
)


class PurchaseValueCorrectionMovingAverageIssueIntegrationError(
    Exception
):
    pass


class PurchaseValueCorrectionMovingAverageIssueIntegrationIntegrityError(
    PurchaseValueCorrectionMovingAverageIssueIntegrationError
):
    pass


class PurchaseValueCorrectionMovingAverageIssueCrossReceiptError(
    PurchaseValueCorrectionMovingAverageIssueIntegrationError
):
    """
    More than one independently corrected physical receipt still
    contributes PVC on-hand value to the same MA stream.

    Same-receipt multi-PVCA is supported by peer replay.

    Cross-receipt nonlinear aggregation is intentionally fail-closed
    until a stream-wide aggregate replay contract is implemented.
    """


@dataclass(
    frozen=True,
    slots=True,
)
class PurchaseValueCorrectionMovingAverageIssueIntegrationResult:
    company_id: int
    product_id: int
    warehouse_id: int

    active_on_hand_allocation_event_ids: tuple[int, ...]

    source_receipt_moving_average_movement_id: int | None

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
            PurchaseValueCorrectionMovingAverageIssueIntegrationIntegrityError(
                f"{field} must be a positive integer"
            )
        )

    return value


def _active_on_hand_allocation_ids(
    history: tuple[
        PurchaseValueCorrectionMovingAverageReplayEvent,
        ...,
    ],
) -> tuple[int, ...]:
    """
    Resolve currently ACTIVE on_hand PVC overlays.

    Reversal rows remain immutable history.
    Only active originals participate.
    """

    _validate_history(
        history
    )

    active = _active_originals(
        history
    )

    ids = []

    for event in active:
        if event.effect_kind != "on_hand":
            continue

        if (
            event.source_moving_average_movement_id
            is not None
            or event.source_inventory_cost_entry_id
            is not None
        ):
            raise (
                PurchaseValueCorrectionMovingAverageIssueIntegrationIntegrityError(
                    "Active on_hand PVC event contains "
                    "ISSUE provenance"
                )
            )

        ids.append(
            _positive_id(
                event.purchase_value_correction_allocation_event_id,
                field="allocation event id",
            )
        )

    if len(ids) != len(set(ids)):
        raise (
            PurchaseValueCorrectionMovingAverageIssueIntegrationIntegrityError(
                "Multiple active on_hand events exist "
                "for one allocation"
            )
        )

    return tuple(
        sorted(ids)
    )


def _validate_single_physical_receipt_group(
    *,
    active_allocation_event_ids: tuple[int, ...],
    peer_source: PurchaseValueCorrectionMovingAveragePeerSource,
) -> int:
    """
    Prove that every ACTIVE on-hand correction belongs to the same
    physical receipt represented by peer_source.

    Same physical receipt:
        supported.

    Different receipt roots:
        fail closed.
    """

    active_ids = set(
        active_allocation_event_ids
    )

    peer_ids = set(
        peer_source.allocation_event_ids
    )

    if not active_ids:
        raise (
            PurchaseValueCorrectionMovingAverageIssueIntegrationIntegrityError(
                "Active allocation set cannot be empty"
            )
        )

    if not active_ids.issubset(
        peer_ids
    ):
        raise (
            PurchaseValueCorrectionMovingAverageIssueCrossReceiptError(
                "Active PVC on-hand overlays span multiple "
                "physical MA receipt roots"
            )
        )

    return _positive_id(
        (
            peer_source
            .base_source
            .source_receipt_moving_average_movement_id
        ),
        field="source receipt MA movement id",
    )


async def reconcile_purchase_value_correction_moving_average_after_issue(
    db: AsyncSession,
    *,
    company_id: int,
    product_id: int,
    warehouse_id: int,
    issue_date: date,
    created_by: int,
) -> PurchaseValueCorrectionMovingAverageIssueIntegrationResult:
    """
    Reconcile PVC MA topology after one normal POSTED MA ISSUE.

    Required caller order:

        1. normal base MovingAverageMovement ISSUE
        2. immutable base InventoryCostEntry
        3. db.flush()
        4. THIS SERVICE — reconciliation only
        5. normal document accounting
        6. typed PVC MA JournalEntry lifecycle

    The normal ISSUE remains base historical truth.

    This service migrates already-existing PVC valuation topology:

        on_hand
            ->
        issued + remaining on_hand

    through immutable reversal/replacement events.

    It does NOT:
        mutate MovingAverageBalance;
        mutate MovingAverageMovement;
        mutate InventoryCostEntry;
        COMMIT;
        ROLLBACK.

    Created immutable PVC MA replay events are returned to the
    posting pipeline. Their typed JournalEntries are posted only
    after normal document accounting creates the historical ISSUE
    JournalEntry.

    Caller owns COMMIT / ROLLBACK.
    """

    company_id = _positive_id(
        company_id,
        field="company_id",
    )

    product_id = _positive_id(
        product_id,
        field="product_id",
    )

    warehouse_id = _positive_id(
        warehouse_id,
        field="warehouse_id",
    )

    created_by = _positive_id(
        created_by,
        field="created_by",
    )

    if not isinstance(
        issue_date,
        date,
    ):
        raise (
            PurchaseValueCorrectionMovingAverageIssueIntegrationIntegrityError(
                "issue_date must be a date"
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
                    PurchaseValueCorrectionMovingAverageReplayEvent.product_id
                    == product_id,
                    PurchaseValueCorrectionMovingAverageReplayEvent.warehouse_id
                    == warehouse_id,
                )
                .order_by(
                    PurchaseValueCorrectionMovingAverageReplayEvent.id
                )
                .with_for_update()
            )
        ).scalars().all()
    )

    active_ids = (
        _active_on_hand_allocation_ids(
            history
        )
    )

    if not active_ids:
        return (
            PurchaseValueCorrectionMovingAverageIssueIntegrationResult(
                company_id=company_id,
                product_id=product_id,
                warehouse_id=warehouse_id,
                active_on_hand_allocation_event_ids=(),
                source_receipt_moving_average_movement_id=None,
                reconciliation_result=None,
            )
        )

    anchor_id = active_ids[0]

    peer_source = (
        await load_purchase_value_correction_moving_average_peer_source(
            db,
            company_id=company_id,
            allocation_event_id=anchor_id,
        )
    )

    source_receipt_id = (
        _validate_single_physical_receipt_group(
            active_allocation_event_ids=active_ids,
            peer_source=peer_source,
        )
    )

    reconciliation = (
        await reconcile_purchase_value_correction_moving_average_peers(
            db,
            company_id=company_id,
            anchor_allocation_event_id=anchor_id,
            adjustment_date=issue_date,
            created_by=created_by,
        )
    )


    return (
        PurchaseValueCorrectionMovingAverageIssueIntegrationResult(
            company_id=company_id,
            product_id=product_id,
            warehouse_id=warehouse_id,
            active_on_hand_allocation_event_ids=active_ids,
            source_receipt_moving_average_movement_id=(
                source_receipt_id
            ),
            reconciliation_result=reconciliation,
        )
    )
