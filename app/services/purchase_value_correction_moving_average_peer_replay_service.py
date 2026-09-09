from dataclasses import dataclass
from datetime import date
from decimal import (
    Decimal,
    ROUND_HALF_UP,
)

from app.services.purchase_value_correction_moving_average_replay_calculation_service import (
    MovingAverageReplayImpact,
    MovingAverageReplayMovement,
    PurchaseValueCorrectionMovingAverageReplayResult,
    calculate_purchase_value_correction_moving_average_replay,
)


ZERO = Decimal("0")
VALUATION_QUANTUM = Decimal("0.00000001")


class PurchaseValueCorrectionMovingAveragePeerReplayError(
    Exception
):
    """Base peer-aware moving-average replay failure."""


class PurchaseValueCorrectionMovingAveragePeerReplayIntegrityError(
    PurchaseValueCorrectionMovingAveragePeerReplayError
):
    """Peer replay input or result is inconsistent."""


@dataclass(
    frozen=True,
    slots=True,
)
class MovingAveragePeerCorrection:
    """
    One active immutable PVCA correction affecting the SAME
    physical MA receipt.

    allocation_value_delta:
        corrected_allocated_base_amount
        - original_allocated_base_amount
    """

    allocation_event_id: int
    recognition_date: date
    allocation_value_delta: Decimal


@dataclass(
    frozen=True,
    slots=True,
)
class MovingAveragePeerAttributedImpact:
    """
    Marginal valuation impact attributed to ONE peer correction.

    original_valuation_amount:
        corrected destination valuation after all earlier peers.

    corrected_valuation_amount:
        corrected destination valuation after this peer.

    Therefore valuation_delta is the exact marginal contribution
    of this peer to that destination.
    """

    allocation_event_id: int
    recognition_date: date

    effect_kind: str
    quantity: Decimal

    source_moving_average_movement_id: int | None
    source_inventory_cost_entry_id: int | None

    original_valuation_amount: Decimal
    corrected_valuation_amount: Decimal

    @property
    def valuation_delta(
        self,
    ) -> Decimal:
        return _valuation(
            self.corrected_valuation_amount
            - self.original_valuation_amount
        )


@dataclass(
    frozen=True,
    slots=True,
)
class MovingAveragePeerReplayStep:
    peer: MovingAveragePeerCorrection

    cumulative_receipt_value_delta: Decimal
    cumulative_corrected_receipt_value: Decimal

    replay_result: (
        PurchaseValueCorrectionMovingAverageReplayResult
    )

    attributed_impacts: tuple[
        MovingAveragePeerAttributedImpact,
        ...,
    ]

    @property
    def attributed_delta_total(
        self,
    ) -> Decimal:
        return _valuation(
            sum(
                (
                    impact.valuation_delta
                    for impact in self.attributed_impacts
                ),
                ZERO,
            )
        )


@dataclass(
    frozen=True,
    slots=True,
)
class PurchaseValueCorrectionMovingAveragePeerReplayResult:
    ordered_peers: tuple[
        MovingAveragePeerCorrection,
        ...,
    ]

    steps: tuple[
        MovingAveragePeerReplayStep,
        ...,
    ]

    final_replay_result: (
        PurchaseValueCorrectionMovingAverageReplayResult
    )

    aggregate_receipt_value_delta: Decimal

    @property
    def attributed_delta_total(
        self,
    ) -> Decimal:
        return _valuation(
            sum(
                (
                    step.attributed_delta_total
                    for step in self.steps
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
        raise (
            PurchaseValueCorrectionMovingAveragePeerReplayIntegrityError(
                "Value must be Decimal-compatible"
            )
        ) from exc

    if not result.is_finite():
        raise (
            PurchaseValueCorrectionMovingAveragePeerReplayIntegrityError(
                "Value must be finite"
            )
        )

    return result.quantize(
        VALUATION_QUANTUM,
        rounding=ROUND_HALF_UP,
    )


def _positive_id(
    value,
    *,
    field: str,
) -> int:
    if (
        isinstance(
            value,
            bool,
        )
        or not isinstance(
            value,
            int,
        )
        or value <= 0
    ):
        raise (
            PurchaseValueCorrectionMovingAveragePeerReplayIntegrityError(
                f"{field} must be a positive integer"
            )
        )

    return value


def _impact_key(
    impact: MovingAverageReplayImpact,
) -> tuple[
    str,
    int | None,
    int | None,
]:
    return (
        impact.effect_kind,
        impact.source_moving_average_movement_id,
        impact.source_inventory_cost_entry_id,
    )


def _validate_peer(
    peer: MovingAveragePeerCorrection,
) -> None:
    _positive_id(
        peer.allocation_event_id,
        field="allocation_event_id",
    )

    if not isinstance(
        peer.recognition_date,
        date,
    ):
        raise (
            PurchaseValueCorrectionMovingAveragePeerReplayIntegrityError(
                "recognition_date must be a date"
            )
        )

    delta = _valuation(
        peer.allocation_value_delta
    )

    if delta == ZERO:
        raise (
            PurchaseValueCorrectionMovingAveragePeerReplayIntegrityError(
                "Active peer correction cannot be a no-op"
            )
        )


def _result_state_map(
    result: PurchaseValueCorrectionMovingAverageReplayResult,
) -> dict[
    tuple[
        str,
        int | None,
        int | None,
    ],
    MovingAverageReplayImpact,
]:
    values = {}

    for impact in result.impacts:
        key = _impact_key(
            impact
        )

        if key in values:
            raise (
                PurchaseValueCorrectionMovingAveragePeerReplayIntegrityError(
                    "Replay result contains duplicate destination key"
                )
            )

        values[
            key
        ] = impact

    return values


def _destination_original_amount(
    *,
    previous: MovingAverageReplayImpact | None,
    current: MovingAverageReplayImpact | None,
) -> Decimal:
    """
    Historical uncorrected destination valuation is stable across
    cumulative replay snapshots.

    If one side is absent, the present snapshot still contains the
    immutable historical original amount.
    """

    source = (
        previous
        if previous is not None
        else current
    )

    if source is None:
        raise (
            PurchaseValueCorrectionMovingAveragePeerReplayIntegrityError(
                "Destination has no replay source state"
            )
        )

    return _valuation(
        source.original_valuation_amount
    )


def _destination_corrected_amount(
    *,
    impact: MovingAverageReplayImpact | None,
    historical_original: Decimal,
) -> Decimal:
    if impact is None:
        return historical_original

    original = _valuation(
        impact.original_valuation_amount
    )

    if original != historical_original:
        raise (
            PurchaseValueCorrectionMovingAveragePeerReplayIntegrityError(
                "Cumulative replay changed historical destination "
                "original valuation"
            )
        )

    return _valuation(
        impact.corrected_valuation_amount
    )


def _destination_quantity(
    *,
    previous: MovingAverageReplayImpact | None,
    current: MovingAverageReplayImpact | None,
) -> Decimal:
    source = (
        current
        if current is not None
        else previous
    )

    if source is None:
        raise (
            PurchaseValueCorrectionMovingAveragePeerReplayIntegrityError(
                "Destination has no quantity source"
            )
        )

    quantity = Decimal(
        str(
            source.quantity
        )
    )

    if (
        previous is not None
        and current is not None
        and Decimal(
            str(
                previous.quantity
            )
        )
        != quantity
    ):
        raise (
            PurchaseValueCorrectionMovingAveragePeerReplayIntegrityError(
                "Replay destination quantity changed between peers"
            )
        )

    if quantity <= ZERO:
        raise (
            PurchaseValueCorrectionMovingAveragePeerReplayIntegrityError(
                "Replay destination quantity must be positive"
            )
        )

    return quantity


def _marginal_impacts(
    *,
    peer: MovingAveragePeerCorrection,
    previous_result: (
        PurchaseValueCorrectionMovingAverageReplayResult
        | None
    ),
    current_result: (
        PurchaseValueCorrectionMovingAverageReplayResult
    ),
) -> tuple[
    MovingAveragePeerAttributedImpact,
    ...,
]:
    previous_map = (
        _result_state_map(
            previous_result
        )
        if previous_result is not None
        else {}
    )

    current_map = _result_state_map(
        current_result
    )

    keys = sorted(
        (
            set(
                previous_map
            )
            | set(
                current_map
            )
        ),
        key=lambda key: (
            key[0],
            key[1] or 0,
            key[2] or 0,
        ),
    )

    attributed = []

    for key in keys:
        previous = previous_map.get(
            key
        )
        current = current_map.get(
            key
        )

        historical_original = (
            _destination_original_amount(
                previous=previous,
                current=current,
            )
        )

        previous_corrected = (
            _destination_corrected_amount(
                impact=previous,
                historical_original=historical_original,
            )
        )

        current_corrected = (
            _destination_corrected_amount(
                impact=current,
                historical_original=historical_original,
            )
        )

        if (
            previous_corrected
            == current_corrected
        ):
            continue

        quantity = _destination_quantity(
            previous=previous,
            current=current,
        )

        effect_kind = key[0]

        if effect_kind not in (
            "issued",
            "on_hand",
        ):
            raise (
                PurchaseValueCorrectionMovingAveragePeerReplayIntegrityError(
                    "Unsupported replay destination kind"
                )
            )

        attributed.append(
            MovingAveragePeerAttributedImpact(
                allocation_event_id=(
                    peer.allocation_event_id
                ),
                recognition_date=(
                    peer.recognition_date
                ),
                effect_kind=effect_kind,
                quantity=quantity,
                source_moving_average_movement_id=(
                    key[1]
                ),
                source_inventory_cost_entry_id=(
                    key[2]
                ),
                original_valuation_amount=(
                    previous_corrected
                ),
                corrected_valuation_amount=(
                    current_corrected
                ),
            )
        )

    result = tuple(
        attributed
    )

    marginal_total = _valuation(
        sum(
            (
                impact.valuation_delta
                for impact in result
            ),
            ZERO,
        )
    )

    expected = _valuation(
        peer.allocation_value_delta
    )

    if marginal_total != expected:
        raise (
            PurchaseValueCorrectionMovingAveragePeerReplayIntegrityError(
                "Peer-attributed MA replay impacts do not conserve "
                "peer allocation delta"
            )
        )

    return result


def calculate_purchase_value_correction_moving_average_peer_replay(
    *,
    opening_quantity,
    opening_inventory_value,
    receipt_quantity,
    original_receipt_value,
    historical_receipt_balance_quantity_after,
    historical_receipt_balance_value_after,
    historical_receipt_average_unit_cost_after,
    later_movements: tuple[
        MovingAverageReplayMovement,
        ...,
    ],
    peers: tuple[
        MovingAveragePeerCorrection,
        ...,
    ],
) -> PurchaseValueCorrectionMovingAveragePeerReplayResult:
    """
    Deterministic peer-aware replay for multiple active PVC
    allocations affecting ONE physical MA receipt.

    Peers are applied cumulatively in deterministic order:

        recognition_date
        -> allocation_event_id

    For each cumulative state we replay the SAME immutable base
    warehouse chronology.

    A peer's attributed impacts are the marginal difference between:

        replay(after this peer)
        -
        replay(after all earlier peers)

    This avoids the invalid assumption that independent MA replay
    impacts can simply be summed.

    No persistence.
    No MovingAverageBalance mutation.
    No MovingAverageMovement mutation.
    """

    peers = tuple(
        peers
    )

    if not peers:
        raise (
            PurchaseValueCorrectionMovingAveragePeerReplayIntegrityError(
                "Peer replay requires at least one active correction"
            )
        )

    seen_ids = set()

    for peer in peers:
        _validate_peer(
            peer
        )

        if (
            peer.allocation_event_id
            in seen_ids
        ):
            raise (
                PurchaseValueCorrectionMovingAveragePeerReplayIntegrityError(
                    "Duplicate active allocation_event_id"
                )
            )

        seen_ids.add(
            peer.allocation_event_id
        )

    ordered = tuple(
        sorted(
            peers,
            key=lambda peer: (
                peer.recognition_date,
                peer.allocation_event_id,
            ),
        )
    )

    original_receipt_value = _valuation(
        original_receipt_value
    )

    if original_receipt_value < ZERO:
        raise (
            PurchaseValueCorrectionMovingAveragePeerReplayIntegrityError(
                "Original receipt value cannot be negative"
            )
        )

    cumulative_delta = ZERO
    previous_result = None
    steps = []

    for peer in ordered:
        cumulative_delta = _valuation(
            cumulative_delta
            + _valuation(
                peer.allocation_value_delta
            )
        )

        cumulative_corrected_receipt_value = _valuation(
            original_receipt_value
            + cumulative_delta
        )

        if (
            cumulative_corrected_receipt_value
            < ZERO
        ):
            raise (
                PurchaseValueCorrectionMovingAveragePeerReplayIntegrityError(
                    "Cumulative PVC peers would make physical "
                    "receipt value negative"
                )
            )

        replay_result = (
            calculate_purchase_value_correction_moving_average_replay(
                opening_quantity=opening_quantity,
                opening_inventory_value=(
                    opening_inventory_value
                ),
                receipt_quantity=receipt_quantity,
                original_receipt_value=(
                    original_receipt_value
                ),
                corrected_receipt_value=(
                    cumulative_corrected_receipt_value
                ),
                historical_receipt_balance_quantity_after=(
                    historical_receipt_balance_quantity_after
                ),
                historical_receipt_balance_value_after=(
                    historical_receipt_balance_value_after
                ),
                historical_receipt_average_unit_cost_after=(
                    historical_receipt_average_unit_cost_after
                ),
                later_movements=later_movements,
            )
        )

        attributed = _marginal_impacts(
            peer=peer,
            previous_result=previous_result,
            current_result=replay_result,
        )

        step = MovingAveragePeerReplayStep(
            peer=peer,
            cumulative_receipt_value_delta=(
                cumulative_delta
            ),
            cumulative_corrected_receipt_value=(
                cumulative_corrected_receipt_value
            ),
            replay_result=replay_result,
            attributed_impacts=attributed,
        )

        if (
            step.attributed_delta_total
            != _valuation(
                peer.allocation_value_delta
            )
        ):
            raise (
                PurchaseValueCorrectionMovingAveragePeerReplayIntegrityError(
                    "Peer replay step failed conservation"
                )
            )

        steps.append(
            step
        )

        previous_result = (
            replay_result
        )

    if previous_result is None:
        raise (
            PurchaseValueCorrectionMovingAveragePeerReplayIntegrityError(
                "Peer replay produced no final state"
            )
        )

    aggregate_delta = _valuation(
        sum(
            (
                _valuation(
                    peer.allocation_value_delta
                )
                for peer in ordered
            ),
            ZERO,
        )
    )

    if (
        previous_result.receipt_value_delta
        != aggregate_delta
    ):
        raise (
            PurchaseValueCorrectionMovingAveragePeerReplayIntegrityError(
                "Final cumulative replay does not conserve "
                "aggregate receipt correction"
            )
        )

    result = (
        PurchaseValueCorrectionMovingAveragePeerReplayResult(
            ordered_peers=ordered,
            steps=tuple(
                steps
            ),
            final_replay_result=(
                previous_result
            ),
            aggregate_receipt_value_delta=(
                aggregate_delta
            ),
        )
    )

    if (
        result.attributed_delta_total
        != aggregate_delta
    ):
        raise (
            PurchaseValueCorrectionMovingAveragePeerReplayIntegrityError(
                "Attributed peer replay total differs from "
                "aggregate correction"
            )
        )

    return result
