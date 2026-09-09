from dataclasses import dataclass
from datetime import date
from decimal import (
    Decimal,
    ROUND_HALF_UP,
)

from app.models.stock_ledger import (
    StockMovementType,
)


ZERO = Decimal("0")
VALUATION_QUANTUM = Decimal(
    "0.00000001"
)


class PurchaseValueCorrectionMovingAverageReplayError(
    Exception
):
    """Pure moving-average replay contract failure."""


@dataclass(
    frozen=True,
    slots=True,
)
class MovingAverageReplayMovement:
    movement_id: int
    movement_date: date
    movement_type: StockMovementType

    quantity_delta: Decimal
    value_delta: Decimal

    balance_quantity_after: Decimal
    balance_value_after: Decimal
    average_unit_cost_after: Decimal

    inventory_cost_entry_id: int | None = None


@dataclass(
    frozen=True,
    slots=True,
)
class MovingAverageReplayImpact:
    effect_kind: str
    quantity: Decimal

    original_valuation_amount: Decimal
    corrected_valuation_amount: Decimal

    source_moving_average_movement_id: int | None
    source_inventory_cost_entry_id: int | None

    @property
    def valuation_delta(
        self,
    ) -> Decimal:
        return (
            self.corrected_valuation_amount
            - self.original_valuation_amount
        )


@dataclass(
    frozen=True,
    slots=True,
)
class MovingAverageReplayState:
    source_moving_average_movement_id: int

    quantity_after: Decimal

    original_value_after: Decimal
    corrected_value_after: Decimal

    original_average_after: Decimal
    corrected_average_after: Decimal


@dataclass(
    frozen=True,
    slots=True,
)
class PurchaseValueCorrectionMovingAverageReplayResult:
    receipt_value_delta: Decimal

    impacts: tuple[
        MovingAverageReplayImpact,
        ...,
    ]

    replay_states: tuple[
        MovingAverageReplayState,
        ...,
    ]

    final_quantity: Decimal
    original_final_value: Decimal
    corrected_final_value: Decimal

    @property
    def impact_delta_total(
        self,
    ) -> Decimal:
        return _valuation(
            sum(
                (
                    impact.valuation_delta
                    for impact in self.impacts
                ),
                ZERO,
            )
        )


def _valuation(
    value,
) -> Decimal:
    try:
        result = Decimal(
            str(value)
        )
    except Exception as exc:
        raise PurchaseValueCorrectionMovingAverageReplayError(
            "Value must be Decimal-compatible"
        ) from exc

    if not result.is_finite():
        raise PurchaseValueCorrectionMovingAverageReplayError(
            "Value must be finite"
        )

    return result.quantize(
        VALUATION_QUANTUM,
        rounding=ROUND_HALF_UP,
    )


def _quantity(
    value,
) -> Decimal:
    try:
        result = Decimal(
            str(value)
        )
    except Exception as exc:
        raise PurchaseValueCorrectionMovingAverageReplayError(
            "Quantity must be Decimal-compatible"
        ) from exc

    if not result.is_finite():
        raise PurchaseValueCorrectionMovingAverageReplayError(
            "Quantity must be finite"
        )

    return result


def _average(
    *,
    quantity: Decimal,
    value: Decimal,
) -> Decimal:
    if quantity < ZERO:
        raise PurchaseValueCorrectionMovingAverageReplayError(
            "Moving-average quantity cannot be negative"
        )

    if value < ZERO:
        raise PurchaseValueCorrectionMovingAverageReplayError(
            "Moving-average value cannot be negative"
        )

    if quantity == ZERO:
        if value != ZERO:
            raise PurchaseValueCorrectionMovingAverageReplayError(
                "Zero quantity cannot retain inventory value"
            )

        return _valuation(
            ZERO
        )

    return _valuation(
        value
        / quantity
    )


def _issue_cost(
    *,
    quantity_before: Decimal,
    value_before: Decimal,
    average_before: Decimal,
    issue_quantity: Decimal,
) -> Decimal:
    if issue_quantity <= ZERO:
        raise PurchaseValueCorrectionMovingAverageReplayError(
            "ISSUE quantity must be positive"
        )

    if issue_quantity > quantity_before:
        raise PurchaseValueCorrectionMovingAverageReplayError(
            "ISSUE exceeds moving-average inventory"
        )

    if issue_quantity == quantity_before:
        return _valuation(
            value_before
        )

    return _valuation(
        issue_quantity
        * average_before
    )


def _validate_historical_after_state(
    *,
    movement: MovingAverageReplayMovement,
    expected_quantity: Decimal,
    expected_value: Decimal,
    expected_average: Decimal,
) -> None:
    if (
        _quantity(
            movement.balance_quantity_after
        )
        != expected_quantity
    ):
        raise PurchaseValueCorrectionMovingAverageReplayError(
            "Historical MA movement quantity after-state mismatch"
        )

    if (
        _valuation(
            movement.balance_value_after
        )
        != expected_value
    ):
        raise PurchaseValueCorrectionMovingAverageReplayError(
            "Historical MA movement value after-state mismatch"
        )

    if (
        _valuation(
            movement.average_unit_cost_after
        )
        != expected_average
    ):
        raise PurchaseValueCorrectionMovingAverageReplayError(
            "Historical MA movement average after-state mismatch"
        )


def calculate_purchase_value_correction_moving_average_replay(
    *,
    opening_quantity,
    opening_inventory_value,
    receipt_quantity,
    original_receipt_value,
    corrected_receipt_value,
    historical_receipt_balance_quantity_after,
    historical_receipt_balance_value_after,
    historical_receipt_average_unit_cost_after,
    later_movements: tuple[
        MovingAverageReplayMovement,
        ...,
    ],
) -> PurchaseValueCorrectionMovingAverageReplayResult:
    """
    Pure deterministic replay for one MA stream.

    Historical MovingAverageMovement rows are never mutated.

    Historical InventoryCostEntry rows are never mutated.

    Supported subsequent active movements:
        RECEIPT
        ISSUE

    ADJUSTMENT currently fails closed until its exact replay
    semantics are explicitly implemented.

    REVERSAL rows must be removed by the future source loader.

    Conservation invariant:

        issued valuation deltas
        + on-hand valuation delta
        =
        corrected receipt value
        - original receipt value
    """

    opening_quantity = _quantity(
        opening_quantity
    )
    opening_inventory_value = _valuation(
        opening_inventory_value
    )

    receipt_quantity = _quantity(
        receipt_quantity
    )

    original_receipt_value = _valuation(
        original_receipt_value
    )
    corrected_receipt_value = _valuation(
        corrected_receipt_value
    )

    if opening_quantity < ZERO:
        raise PurchaseValueCorrectionMovingAverageReplayError(
            "Opening quantity cannot be negative"
        )

    if opening_inventory_value < ZERO:
        raise PurchaseValueCorrectionMovingAverageReplayError(
            "Opening value cannot be negative"
        )

    if receipt_quantity <= ZERO:
        raise PurchaseValueCorrectionMovingAverageReplayError(
            "Receipt quantity must be positive"
        )

    if original_receipt_value < ZERO:
        raise PurchaseValueCorrectionMovingAverageReplayError(
            "Original receipt value cannot be negative"
        )

    if corrected_receipt_value < ZERO:
        raise PurchaseValueCorrectionMovingAverageReplayError(
            "Corrected receipt value cannot be negative"
        )

    if (
        original_receipt_value
        == corrected_receipt_value
    ):
        raise PurchaseValueCorrectionMovingAverageReplayError(
            "Receipt value correction cannot be a no-op"
        )

    if (
        opening_quantity == ZERO
        and opening_inventory_value != ZERO
    ):
        raise PurchaseValueCorrectionMovingAverageReplayError(
            "Opening MA state is inconsistent"
        )

    original_quantity = (
        opening_quantity
        + receipt_quantity
    )
    corrected_quantity = (
        original_quantity
    )

    original_value = _valuation(
        opening_inventory_value
        + original_receipt_value
    )
    corrected_value = _valuation(
        opening_inventory_value
        + corrected_receipt_value
    )

    original_average = _average(
        quantity=original_quantity,
        value=original_value,
    )
    corrected_average = _average(
        quantity=corrected_quantity,
        value=corrected_value,
    )

    if (
        _quantity(
            historical_receipt_balance_quantity_after
        )
        != original_quantity
    ):
        raise PurchaseValueCorrectionMovingAverageReplayError(
            "Historical receipt quantity after-state mismatch"
        )

    if (
        _valuation(
            historical_receipt_balance_value_after
        )
        != original_value
    ):
        raise PurchaseValueCorrectionMovingAverageReplayError(
            "Historical receipt value after-state mismatch"
        )

    if (
        _valuation(
            historical_receipt_average_unit_cost_after
        )
        != original_average
    ):
        raise PurchaseValueCorrectionMovingAverageReplayError(
            "Historical receipt average after-state mismatch"
        )

    impacts: list[
        MovingAverageReplayImpact
    ] = []

    replay_states: list[
        MovingAverageReplayState
    ] = []

    previous_id = 0

    for movement in later_movements:
        if (
            not isinstance(
                movement.movement_id,
                int,
            )
            or isinstance(
                movement.movement_id,
                bool,
            )
            or movement.movement_id <= previous_id
        ):
            raise PurchaseValueCorrectionMovingAverageReplayError(
                "Later MA movements must be strictly ordered by ID"
            )

        previous_id = (
            movement.movement_id
        )

        movement_quantity_delta = _quantity(
            movement.quantity_delta
        )
        movement_value_delta = _valuation(
            movement.value_delta
        )

        if (
            movement.movement_type
            == StockMovementType.RECEIPT
        ):
            if movement_quantity_delta <= ZERO:
                raise PurchaseValueCorrectionMovingAverageReplayError(
                    "RECEIPT quantity_delta must be positive"
                )

            if movement_value_delta < ZERO:
                raise PurchaseValueCorrectionMovingAverageReplayError(
                    "RECEIPT value_delta cannot be negative"
                )

            original_quantity = (
                original_quantity
                + movement_quantity_delta
            )
            corrected_quantity = (
                corrected_quantity
                + movement_quantity_delta
            )

            original_value = _valuation(
                original_value
                + movement_value_delta
            )
            corrected_value = _valuation(
                corrected_value
                + movement_value_delta
            )

            original_average = _average(
                quantity=original_quantity,
                value=original_value,
            )
            corrected_average = _average(
                quantity=corrected_quantity,
                value=corrected_value,
            )

        elif (
            movement.movement_type
            == StockMovementType.ISSUE
        ):
            if movement_quantity_delta >= ZERO:
                raise PurchaseValueCorrectionMovingAverageReplayError(
                    "ISSUE quantity_delta must be negative"
                )

            if movement_value_delta > ZERO:
                raise PurchaseValueCorrectionMovingAverageReplayError(
                    "ISSUE value_delta must be non-positive"
                )

            issue_quantity = (
                -movement_quantity_delta
            )

            historical_issue_cost = (
                -movement_value_delta
            )

            if movement.inventory_cost_entry_id is None:
                raise PurchaseValueCorrectionMovingAverageReplayError(
                    "ISSUE replay source requires InventoryCostEntry ID"
                )

            original_issue_cost = _issue_cost(
                quantity_before=original_quantity,
                value_before=original_value,
                average_before=original_average,
                issue_quantity=issue_quantity,
            )

            if (
                original_issue_cost
                != historical_issue_cost
            ):
                raise PurchaseValueCorrectionMovingAverageReplayError(
                    "Historical ISSUE value_delta does not match "
                    "moving-average source state"
                )

            corrected_issue_cost = _issue_cost(
                quantity_before=corrected_quantity,
                value_before=corrected_value,
                average_before=corrected_average,
                issue_quantity=issue_quantity,
            )

            original_quantity = (
                original_quantity
                - issue_quantity
            )
            corrected_quantity = (
                corrected_quantity
                - issue_quantity
            )

            original_value = _valuation(
                original_value
                - original_issue_cost
            )
            corrected_value = _valuation(
                corrected_value
                - corrected_issue_cost
            )

            original_average = _average(
                quantity=original_quantity,
                value=original_value,
            )
            corrected_average = _average(
                quantity=corrected_quantity,
                value=corrected_value,
            )

            if (
                corrected_issue_cost
                != original_issue_cost
            ):
                impacts.append(
                    MovingAverageReplayImpact(
                        effect_kind="issued",
                        quantity=issue_quantity,
                        original_valuation_amount=(
                            original_issue_cost
                        ),
                        corrected_valuation_amount=(
                            corrected_issue_cost
                        ),
                        source_moving_average_movement_id=(
                            movement.movement_id
                        ),
                        source_inventory_cost_entry_id=(
                            movement.inventory_cost_entry_id
                        ),
                    )
                )

        elif (
            movement.movement_type
            == StockMovementType.ADJUSTMENT
        ):
            raise PurchaseValueCorrectionMovingAverageReplayError(
                "Later MA ADJUSTMENT replay semantics "
                "are not implemented yet"
            )

        elif (
            movement.movement_type
            == StockMovementType.REVERSAL
        ):
            raise PurchaseValueCorrectionMovingAverageReplayError(
                "REVERSED MA rows must be excluded before replay"
            )

        else:
            raise PurchaseValueCorrectionMovingAverageReplayError(
                "Unsupported MA movement type"
            )

        _validate_historical_after_state(
            movement=movement,
            expected_quantity=original_quantity,
            expected_value=original_value,
            expected_average=original_average,
        )

        replay_states.append(
            MovingAverageReplayState(
                source_moving_average_movement_id=(
                    movement.movement_id
                ),
                quantity_after=(
                    corrected_quantity
                ),
                original_value_after=(
                    original_value
                ),
                corrected_value_after=(
                    corrected_value
                ),
                original_average_after=(
                    original_average
                ),
                corrected_average_after=(
                    corrected_average
                ),
            )
        )

    if (
        original_quantity
        != corrected_quantity
    ):
        raise PurchaseValueCorrectionMovingAverageReplayError(
            "Corrected replay changed physical quantity"
        )

    final_quantity = (
        original_quantity
    )

    if (
        final_quantity > ZERO
        and corrected_value != original_value
    ):
        impacts.append(
            MovingAverageReplayImpact(
                effect_kind="on_hand",
                quantity=final_quantity,
                original_valuation_amount=(
                    original_value
                ),
                corrected_valuation_amount=(
                    corrected_value
                ),
                source_moving_average_movement_id=None,
                source_inventory_cost_entry_id=None,
            )
        )

    if (
        final_quantity == ZERO
        and (
            original_value != ZERO
            or corrected_value != ZERO
        )
    ):
        raise PurchaseValueCorrectionMovingAverageReplayError(
            "Zero final quantity must have zero replay value"
        )

    receipt_delta = _valuation(
        corrected_receipt_value
        - original_receipt_value
    )

    impact_total = _valuation(
        sum(
            (
                impact.valuation_delta
                for impact in impacts
            ),
            ZERO,
        )
    )

    if impact_total != receipt_delta:
        raise PurchaseValueCorrectionMovingAverageReplayError(
            "MA replay value correction does not conserve "
            "receipt valuation delta"
        )

    return PurchaseValueCorrectionMovingAverageReplayResult(
        receipt_value_delta=receipt_delta,
        impacts=tuple(
            impacts
        ),
        replay_states=tuple(
            replay_states
        ),
        final_quantity=final_quantity,
        original_final_value=original_value,
        corrected_final_value=corrected_value,
    )
