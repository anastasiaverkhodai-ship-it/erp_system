from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.inventory_cost_entry import InventoryCostEntry
from app.models.moving_average_movement import MovingAverageMovement
from app.models.purchase_value_correction_allocation_event import (
    PurchaseValueCorrectionAllocationEvent,
)
from app.models.purchase_value_correction_moving_average_replay_event import (
    PurchaseValueCorrectionMovingAverageReplayEvent,
)
from app.models.stock_ledger import StockMovementType


ZERO = Decimal("0")


class PurchaseValueCorrectionMovingAverageReplayPersistenceError(
    Exception
):
    pass


class PurchaseValueCorrectionMovingAverageReplayDataIntegrityError(
    PurchaseValueCorrectionMovingAverageReplayPersistenceError
):
    pass


class PurchaseValueCorrectionMovingAverageReplaySourceNotFoundError(
    PurchaseValueCorrectionMovingAverageReplayPersistenceError
):
    pass


class PurchaseValueCorrectionMovingAverageReplaySourceStateError(
    PurchaseValueCorrectionMovingAverageReplayPersistenceError
):
    pass


class PurchaseValueCorrectionMovingAverageReplayChronologyError(
    PurchaseValueCorrectionMovingAverageReplayPersistenceError
):
    pass


@dataclass(
    frozen=True,
    slots=True,
)
class PurchaseValueCorrectionMovingAverageReplayTarget:
    purchase_value_correction_allocation_event_id: int
    product_id: int
    warehouse_id: int
    effect_kind: str

    source_moving_average_movement_id: int | None
    source_inventory_cost_entry_id: int | None

    recognition_date: date

    quantity: Decimal
    original_valuation_amount: Decimal
    corrected_valuation_amount: Decimal

    currency_code: str

    @property
    def valuation_delta(
        self,
    ) -> Decimal:
        return (
            self.corrected_valuation_amount
            - self.original_valuation_amount
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
        raise PurchaseValueCorrectionMovingAverageReplayDataIntegrityError(
            f"{field} must be a positive integer"
        )

    return value


def _decimal(
    value,
    *,
    field: str,
) -> Decimal:
    try:
        result = Decimal(
            str(value)
        )
    except Exception as exc:
        raise PurchaseValueCorrectionMovingAverageReplayDataIntegrityError(
            f"{field} must be Decimal-compatible"
        ) from exc

    if not result.is_finite():
        raise PurchaseValueCorrectionMovingAverageReplayDataIntegrityError(
            f"{field} must be finite"
        )

    return result


def _enum_value(
    value,
) -> str:
    return str(
        getattr(
            value,
            "value",
            value,
        )
    ).lower()


def _target_key(
    target: PurchaseValueCorrectionMovingAverageReplayTarget,
) -> tuple:
    return (
        target.effect_kind,
        target.source_moving_average_movement_id,
        target.source_inventory_cost_entry_id,
    )


def _event_key(
    event: PurchaseValueCorrectionMovingAverageReplayEvent,
) -> tuple:
    return (
        event.effect_kind,
        event.source_moving_average_movement_id,
        event.source_inventory_cost_entry_id,
    )


def _target_is_noop(
    target: PurchaseValueCorrectionMovingAverageReplayTarget,
) -> bool:
    return (
        _decimal(
            target.original_valuation_amount,
            field="original_valuation_amount",
        )
        ==
        _decimal(
            target.corrected_valuation_amount,
            field="corrected_valuation_amount",
        )
    )


def _validate_target_shape(
    target: PurchaseValueCorrectionMovingAverageReplayTarget,
) -> None:
    _positive_id(
        target.purchase_value_correction_allocation_event_id,
        field=(
            "purchase_value_correction_"
            "allocation_event_id"
        ),
    )

    _positive_id(
        target.product_id,
        field="product_id",
    )

    _positive_id(
        target.warehouse_id,
        field="warehouse_id",
    )

    if not isinstance(
        target.recognition_date,
        date,
    ):
        raise PurchaseValueCorrectionMovingAverageReplayDataIntegrityError(
            "recognition_date must be a date"
        )

    quantity = _decimal(
        target.quantity,
        field="quantity",
    )

    if quantity <= ZERO:
        raise PurchaseValueCorrectionMovingAverageReplayDataIntegrityError(
            "quantity must be greater than zero"
        )

    original = _decimal(
        target.original_valuation_amount,
        field="original_valuation_amount",
    )

    corrected = _decimal(
        target.corrected_valuation_amount,
        field="corrected_valuation_amount",
    )

    if (
        original < ZERO
        or corrected < ZERO
    ):
        raise PurchaseValueCorrectionMovingAverageReplayDataIntegrityError(
            "valuation amounts cannot be negative"
        )

    if (
        not isinstance(
            target.currency_code,
            str,
        )
        or len(
            target.currency_code
        ) != 3
    ):
        raise PurchaseValueCorrectionMovingAverageReplayDataIntegrityError(
            "currency_code must contain exactly 3 characters"
        )

    if target.effect_kind == "issued":
        _positive_id(
            target.source_moving_average_movement_id,
            field=(
                "source_moving_average_movement_id"
            ),
        )

        _positive_id(
            target.source_inventory_cost_entry_id,
            field=(
                "source_inventory_cost_entry_id"
            ),
        )

    elif target.effect_kind == "on_hand":
        if (
            target.source_moving_average_movement_id
            is not None
            or target.source_inventory_cost_entry_id
            is not None
        ):
            raise PurchaseValueCorrectionMovingAverageReplayDataIntegrityError(
                "on_hand target cannot contain ISSUE provenance"
            )

    else:
        raise PurchaseValueCorrectionMovingAverageReplayDataIntegrityError(
            "effect_kind must be issued or on_hand"
        )


def _validate_history(
    events: tuple[
        PurchaseValueCorrectionMovingAverageReplayEvent,
        ...,
    ],
) -> None:
    by_id = {}

    for event in events:
        event_id = _positive_id(
            event.id,
            field="MA replay event id",
        )

        if event_id in by_id:
            raise PurchaseValueCorrectionMovingAverageReplayDataIntegrityError(
                "Duplicate MA replay event id"
            )

        by_id[
            event_id
        ] = event

    reversals_by_original = {}

    for event in events:
        if event.reversal_of_id is None:
            continue

        original = by_id.get(
            event.reversal_of_id
        )

        if original is None:
            raise PurchaseValueCorrectionMovingAverageReplayDataIntegrityError(
                "MA replay reversal references unloaded original"
            )

        if original.reversal_of_id is not None:
            raise PurchaseValueCorrectionMovingAverageReplayDataIntegrityError(
                "MA replay reversal cannot reverse another reversal"
            )

        if event.reversal_of_id in reversals_by_original:
            raise PurchaseValueCorrectionMovingAverageReplayDataIntegrityError(
                "Multiple MA replay reversals exist for one original"
            )

        reversals_by_original[
            event.reversal_of_id
        ] = event

        if (
            event.company_id
            != original.company_id
            or (
                event.purchase_value_correction_allocation_event_id
                != original.purchase_value_correction_allocation_event_id
            )
            or event.product_id
            != original.product_id
            or event.warehouse_id
            != original.warehouse_id
            or _event_key(event)
            != _event_key(original)
            or Decimal(str(event.quantity))
            != Decimal(str(original.quantity))
            or str(event.currency_code).upper()
            != str(original.currency_code).upper()
        ):
            raise PurchaseValueCorrectionMovingAverageReplayDataIntegrityError(
                "MA replay reversal changed source provenance"
            )

        if (
            Decimal(
                str(event.original_valuation_amount)
            )
            != Decimal(
                str(original.corrected_valuation_amount)
            )
            or Decimal(
                str(event.corrected_valuation_amount)
            )
            != Decimal(
                str(original.original_valuation_amount)
            )
        ):
            raise PurchaseValueCorrectionMovingAverageReplayDataIntegrityError(
                "MA replay reversal does not invert valuation"
            )

        if (
            event.recognition_date
            < original.recognition_date
        ):
            raise PurchaseValueCorrectionMovingAverageReplayChronologyError(
                "MA replay reversal predates original"
            )


def _active_originals(
    events: tuple[
        PurchaseValueCorrectionMovingAverageReplayEvent,
        ...,
    ],
) -> tuple[
    PurchaseValueCorrectionMovingAverageReplayEvent,
    ...,
]:
    reversed_ids = {
        event.reversal_of_id
        for event in events
        if event.reversal_of_id is not None
    }

    return tuple(
        event
        for event in events
        if (
            event.reversal_of_id is None
            and event.id not in reversed_ids
        )
    )


def _event_matches_target(
    *,
    event: PurchaseValueCorrectionMovingAverageReplayEvent,
    target: PurchaseValueCorrectionMovingAverageReplayTarget,
) -> bool:
    return (
        _event_key(event)
        == _target_key(target)
        and event.product_id
        == target.product_id
        and event.warehouse_id
        == target.warehouse_id
        and event.recognition_date
        == target.recognition_date
        and Decimal(str(event.quantity))
        == _decimal(
            target.quantity,
            field="quantity",
        )
        and Decimal(
            str(
                event.original_valuation_amount
            )
        )
        == _decimal(
            target.original_valuation_amount,
            field="original_valuation_amount",
        )
        and Decimal(
            str(
                event.corrected_valuation_amount
            )
        )
        == _decimal(
            target.corrected_valuation_amount,
            field="corrected_valuation_amount",
        )
        and str(
            event.currency_code
        ).upper()
        == target.currency_code.upper()
    )


def _same_state_except_date(
    *,
    event: PurchaseValueCorrectionMovingAverageReplayEvent,
    target: PurchaseValueCorrectionMovingAverageReplayTarget,
) -> bool:
    return (
        _event_key(event)
        == _target_key(target)
        and event.product_id
        == target.product_id
        and event.warehouse_id
        == target.warehouse_id
        and Decimal(str(event.quantity))
        == Decimal(str(target.quantity))
        and Decimal(
            str(
                event.original_valuation_amount
            )
        )
        == Decimal(
            str(
                target.original_valuation_amount
            )
        )
        and Decimal(
            str(
                event.corrected_valuation_amount
            )
        )
        == Decimal(
            str(
                target.corrected_valuation_amount
            )
        )
        and str(
            event.currency_code
        ).upper()
        == str(
            target.currency_code
        ).upper()
    )


async def _load_history_for_update(
    db: AsyncSession,
    *,
    company_id: int,
    allocation_event_id: int,
) -> tuple[
    PurchaseValueCorrectionMovingAverageReplayEvent,
    ...,
]:
    rows = (
        await db.execute(
            select(
                PurchaseValueCorrectionMovingAverageReplayEvent
            )
            .where(
                PurchaseValueCorrectionMovingAverageReplayEvent.company_id
                == company_id,
                PurchaseValueCorrectionMovingAverageReplayEvent
                .purchase_value_correction_allocation_event_id
                == allocation_event_id,
            )
            .order_by(
                PurchaseValueCorrectionMovingAverageReplayEvent.id
            )
            .with_for_update()
        )
    ).scalars().all()

    return tuple(
        rows
    )


async def _validate_positive_target_sources(
    db: AsyncSession,
    *,
    company_id: int,
    target: PurchaseValueCorrectionMovingAverageReplayTarget,
) -> None:
    allocation = (
        await db.execute(
            select(
                PurchaseValueCorrectionAllocationEvent
            )
            .where(
                PurchaseValueCorrectionAllocationEvent.company_id
                == company_id,
                PurchaseValueCorrectionAllocationEvent.id
                == (
                    target
                    .purchase_value_correction_allocation_event_id
                ),
            )
            .with_for_update()
        )
    ).scalar_one_or_none()

    if allocation is None:
        raise PurchaseValueCorrectionMovingAverageReplaySourceNotFoundError(
            "PVCA source was not found"
        )

    if allocation.reversal_of_id is not None:
        raise PurchaseValueCorrectionMovingAverageReplaySourceStateError(
            "PVCA reversal cannot be positive MA replay source"
        )

    reversal_ids = (
        await db.execute(
            select(
                PurchaseValueCorrectionAllocationEvent.id
            )
            .where(
                PurchaseValueCorrectionAllocationEvent.company_id
                == company_id,
                PurchaseValueCorrectionAllocationEvent.reversal_of_id
                == allocation.id,
            )
        )
    ).scalars().all()

    if len(
        reversal_ids
    ) > 1:
        raise PurchaseValueCorrectionMovingAverageReplayDataIntegrityError(
            "PVCA source has multiple reversals"
        )

    if reversal_ids:
        raise PurchaseValueCorrectionMovingAverageReplaySourceStateError(
            "PVCA source is no longer active"
        )

    if (
        str(
            allocation.currency_code
        ).upper()
        != target.currency_code.upper()
    ):
        raise PurchaseValueCorrectionMovingAverageReplayDataIntegrityError(
            "MA replay currency does not match PVCA"
        )

    if target.effect_kind == "on_hand":
        return

    movement = (
        await db.execute(
            select(
                MovingAverageMovement
            )
            .where(
                MovingAverageMovement.id
                == target.source_moving_average_movement_id,
                MovingAverageMovement.company_id
                == company_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()

    if movement is None:
        raise PurchaseValueCorrectionMovingAverageReplaySourceNotFoundError(
            "source MovingAverageMovement was not found"
        )

    if (
        movement.product_id
        != target.product_id
        or movement.warehouse_id
        != target.warehouse_id
    ):
        raise PurchaseValueCorrectionMovingAverageReplayDataIntegrityError(
            "MA replay movement stream provenance mismatch"
        )

    if (
        _enum_value(
            movement.movement_type
        )
        != StockMovementType.ISSUE.value
    ):
        raise PurchaseValueCorrectionMovingAverageReplaySourceStateError(
            "issued MA replay source is not ISSUE"
        )

    if (
        target.recognition_date
        < movement.movement_date
    ):
        raise PurchaseValueCorrectionMovingAverageReplayChronologyError(
            "issued MA replay impact predates ISSUE"
        )

    cost_entry = (
        await db.execute(
            select(
                InventoryCostEntry
            )
            .where(
                InventoryCostEntry.id
                == target.source_inventory_cost_entry_id,
                InventoryCostEntry.company_id
                == company_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()

    if cost_entry is None:
        raise PurchaseValueCorrectionMovingAverageReplaySourceNotFoundError(
            "source InventoryCostEntry was not found"
        )

    if (
        cost_entry.document_id
        != movement.document_id
        or cost_entry.document_line_id
        != movement.document_line_id
    ):
        raise PurchaseValueCorrectionMovingAverageReplayDataIntegrityError(
            "InventoryCostEntry does not match MA ISSUE"
        )

    if (
        _enum_value(
            cost_entry.valuation_method
        )
        != "weighted_average_moving"
    ):
        raise PurchaseValueCorrectionMovingAverageReplayDataIntegrityError(
            "InventoryCostEntry valuation method is not moving average"
        )


def _new_event_from_target(
    *,
    company_id: int,
    target: PurchaseValueCorrectionMovingAverageReplayTarget,
    created_by: int,
    recognition_date: date,
) -> PurchaseValueCorrectionMovingAverageReplayEvent:
    return PurchaseValueCorrectionMovingAverageReplayEvent(
        company_id=company_id,
        purchase_value_correction_allocation_event_id=(
            target
            .purchase_value_correction_allocation_event_id
        ),
        product_id=target.product_id,
        warehouse_id=target.warehouse_id,
        effect_kind=target.effect_kind,
        source_moving_average_movement_id=(
            target.source_moving_average_movement_id
        ),
        source_inventory_cost_entry_id=(
            target.source_inventory_cost_entry_id
        ),
        recognition_date=recognition_date,
        quantity=_decimal(
            target.quantity,
            field="quantity",
        ),
        original_valuation_amount=_decimal(
            target.original_valuation_amount,
            field="original_valuation_amount",
        ),
        corrected_valuation_amount=_decimal(
            target.corrected_valuation_amount,
            field="corrected_valuation_amount",
        ),
        currency_code=(
            target.currency_code.upper()
        ),
        created_by=created_by,
        reversal_of_id=None,
    )


def _new_reversal(
    *,
    original: PurchaseValueCorrectionMovingAverageReplayEvent,
    created_by: int,
    reversal_date: date,
) -> PurchaseValueCorrectionMovingAverageReplayEvent:
    return PurchaseValueCorrectionMovingAverageReplayEvent(
        company_id=original.company_id,
        purchase_value_correction_allocation_event_id=(
            original
            .purchase_value_correction_allocation_event_id
        ),
        product_id=original.product_id,
        warehouse_id=original.warehouse_id,
        effect_kind=original.effect_kind,
        source_moving_average_movement_id=(
            original.source_moving_average_movement_id
        ),
        source_inventory_cost_entry_id=(
            original.source_inventory_cost_entry_id
        ),
        recognition_date=reversal_date,
        quantity=Decimal(
            str(
                original.quantity
            )
        ),
        original_valuation_amount=Decimal(
            str(
                original.corrected_valuation_amount
            )
        ),
        corrected_valuation_amount=Decimal(
            str(
                original.original_valuation_amount
            )
        ),
        currency_code=(
            str(
                original.currency_code
            ).upper()
        ),
        created_by=created_by,
        reversal_of_id=original.id,
    )


async def reconcile_purchase_value_correction_moving_average_replay_source(
    db: AsyncSession,
    *,
    company_id: int,
    target: PurchaseValueCorrectionMovingAverageReplayTarget,
    created_by: int,
    reversal_date: date | None = None,
) -> tuple[
    PurchaseValueCorrectionMovingAverageReplayEvent,
    ...,
]:
    """
    Immutable source-level reconciliation.

    no current + no-op -> nothing
    no current + positive -> original
    exact current -> nothing
    current + no-op -> reversal
    changed current -> reversal + replacement

    Caller owns transaction.
    """

    company_id = _positive_id(
        company_id,
        field="company_id",
    )

    created_by = _positive_id(
        created_by,
        field="created_by",
    )

    _validate_target_shape(
        target
    )

    history = await _load_history_for_update(
        db,
        company_id=company_id,
        allocation_event_id=(
            target
            .purchase_value_correction_allocation_event_id
        ),
    )

    _validate_history(
        history
    )

    key = _target_key(
        target
    )

    active_matches = tuple(
        event
        for event in _active_originals(
            history
        )
        if _event_key(
            event
        ) == key
    )

    if len(
        active_matches
    ) > 1:
        raise PurchaseValueCorrectionMovingAverageReplayDataIntegrityError(
            "Multiple active MA replay originals exist for one key"
        )

    current = (
        active_matches[0]
        if active_matches
        else None
    )

    target_noop = _target_is_noop(
        target
    )

    if current is None:
        if target_noop:
            return ()

        await _validate_positive_target_sources(
            db,
            company_id=company_id,
            target=target,
        )

        original = _new_event_from_target(
            company_id=company_id,
            target=target,
            created_by=created_by,
            recognition_date=(
                target.recognition_date
            ),
        )

        db.add(
            original
        )
        await db.flush()

        return (
            original,
        )

    if (
        not target_noop
        and _event_matches_target(
            event=current,
            target=target,
        )
    ):
        return ()

    if reversal_date is None:
        raise PurchaseValueCorrectionMovingAverageReplayChronologyError(
            "Changing active MA replay impact requires reversal_date"
        )

    if not isinstance(
        reversal_date,
        date,
    ):
        raise PurchaseValueCorrectionMovingAverageReplayChronologyError(
            "reversal_date must be a date"
        )

    if (
        reversal_date
        < current.recognition_date
    ):
        raise PurchaseValueCorrectionMovingAverageReplayChronologyError(
            "MA replay reversal cannot predate active original"
        )

    reversal = _new_reversal(
        original=current,
        created_by=created_by,
        reversal_date=reversal_date,
    )

    db.add(
        reversal
    )
    await db.flush()

    if target_noop:
        return (
            reversal,
        )

    await _validate_positive_target_sources(
        db,
        company_id=company_id,
        target=target,
    )

    if (
        reversal_date
        < target.recognition_date
    ):
        raise PurchaseValueCorrectionMovingAverageReplayChronologyError(
            "Forward replacement cannot predate primary recognition date"
        )

    replacement = _new_event_from_target(
        company_id=company_id,
        target=target,
        created_by=created_by,
        recognition_date=reversal_date,
    )

    db.add(
        replacement
    )
    await db.flush()

    return (
        reversal,
        replacement,
    )
