from dataclasses import dataclass
from datetime import date
from decimal import (
    Decimal,
    ROUND_HALF_UP,
)
from typing import Iterable


ZERO = Decimal("0")

MONEY_QUANTUM = Decimal("0.01")
UNIT_COST_QUANTUM = Decimal("0.00000001")
VALUATION_QUANTUM = Decimal("0.00000001")

FIFO = "fifo"
WEIGHTED_AVERAGE_MOVING = (
    "weighted_average_moving"
)

SUPPORTED_METHODS = {
    FIFO,
    WEIGHTED_AVERAGE_MOVING,
}


class SalesReturnCostCalculationError(
    Exception
):
    """Base pure Sales Return cost-calculation error."""


class SalesReturnCostDataIntegrityError(
    SalesReturnCostCalculationError
):
    """Historical inventory-cost data is inconsistent."""


class SalesReturnCostCapacityError(
    SalesReturnCostCalculationError
):
    """Returned quantity exceeds original ISSUE capacity."""


class SalesReturnCostChronologyError(
    SalesReturnCostCalculationError
):
    """A return precedes the original inventory ISSUE."""


class SalesReturnCostMethodError(
    SalesReturnCostCalculationError
):
    """Inventory valuation method is unsupported."""


@dataclass(
    frozen=True,
    slots=True,
)
class SalesReturnIssueCostSource:
    """
    Aggregate immutable historical ISSUE-cost source.

    source_id:
        InventoryCostEntry.id

    issue_date:
        original warehouse ISSUE document date

    quantity / unit_cost / valuation_amount / cost_amount:
        immutable InventoryCostEntry economic truth

    valuation_method:
        fifo
        weighted_average_moving
    """

    source_id: int
    issue_date: date
    valuation_method: str
    quantity: Decimal
    unit_cost: Decimal
    valuation_amount: Decimal
    cost_amount: Decimal


@dataclass(
    frozen=True,
    slots=True,
)
class SalesReturnFifoCostSlice:
    """
    One original FIFO consumption slice.

    source_id:
        StockLotConsumption.id

    stock_lot_id:
        original consumed StockLot.id

    quantity / unit_cost:
        immutable historical consumption values
    """

    source_id: int
    stock_lot_id: int
    quantity: Decimal
    unit_cost: Decimal


@dataclass(
    frozen=True,
    slots=True,
)
class SalesReturnCostCandidate:
    """
    One active physical Sales Return quantity.

    return_source_id:
        TradeReturnEvent.id
    """

    return_source_id: int
    event_date: date
    quantity: Decimal


@dataclass(
    frozen=True,
    slots=True,
)
class SalesReturnFifoSliceTarget:
    """
    Exact portion of one original FIFO consumption restored by
    one TradeReturnEvent.

    valuation_amount is 8-decimal inventory valuation precision.
    """

    fifo_consumption_id: int
    stock_lot_id: int
    quantity: Decimal
    unit_cost: Decimal
    valuation_amount: Decimal


@dataclass(
    frozen=True,
    slots=True,
)
class SalesReturnCostTarget:
    """
    Complete desired historical-cost restoration for one return.

    restored_valuation_amount:
        inventory valuation precision, 8 decimals

    restored_cost_amount:
        accounting amount, 2 decimals

    aggregate_historical_unit_cost:
        one aggregate 8-decimal historical cost for the physical
        return quantity. This does NOT erase FIFO slice provenance.

    fifo_slices:
        empty for moving-average
        one or more immutable historical slices for FIFO
    """

    return_source_id: int
    inventory_cost_entry_id: int
    event_date: date
    valuation_method: str
    restored_quantity: Decimal
    restored_valuation_amount: Decimal
    restored_cost_amount: Decimal
    aggregate_historical_unit_cost: Decimal
    fifo_slices: tuple[
        SalesReturnFifoSliceTarget,
        ...,
    ]

    @property
    def pair_key(
        self,
    ) -> tuple[
        int,
        int,
    ]:
        return (
            self.return_source_id,
            self.inventory_cost_entry_id,
        )


def _decimal(
    value,
    *,
    field: str,
) -> Decimal:
    try:
        result = Decimal(
            str(
                value
            )
        )
    except Exception as exc:
        raise SalesReturnCostDataIntegrityError(
            f"{field} must be Decimal-compatible"
        ) from exc

    if not result.is_finite():
        raise SalesReturnCostDataIntegrityError(
            f"{field} must be finite"
        )

    return result


def _positive_id(
    value: int,
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
        raise SalesReturnCostDataIntegrityError(
            f"{field} must be greater than zero"
        )

    return value


def _money(
    value: Decimal,
) -> Decimal:
    return Decimal(
        value
    ).quantize(
        MONEY_QUANTUM,
        rounding=ROUND_HALF_UP,
    )


def _unit_cost(
    value: Decimal,
) -> Decimal:
    return Decimal(
        value
    ).quantize(
        UNIT_COST_QUANTUM,
        rounding=ROUND_HALF_UP,
    )


def _valuation_amount(
    value: Decimal,
) -> Decimal:
    return Decimal(
        value
    ).quantize(
        VALUATION_QUANTUM,
        rounding=ROUND_HALF_UP,
    )


def _method_value(
    value,
) -> str:
    raw = getattr(
        value,
        "value",
        value,
    )

    return str(
        raw
    ).strip().lower()


def _normalize_source(
    source: SalesReturnIssueCostSource,
) -> SalesReturnIssueCostSource:
    if not isinstance(
        source,
        SalesReturnIssueCostSource,
    ):
        raise SalesReturnCostDataIntegrityError(
            "source must be SalesReturnIssueCostSource"
        )

    source_id = _positive_id(
        source.source_id,
        field="source_id",
    )

    if not isinstance(
        source.issue_date,
        date,
    ):
        raise SalesReturnCostDataIntegrityError(
            "issue_date must be a date"
        )

    method = _method_value(
        source.valuation_method
    )

    if method not in SUPPORTED_METHODS:
        raise SalesReturnCostMethodError(
            "Unsupported inventory valuation method: "
            f"{method}"
        )

    quantity = _decimal(
        source.quantity,
        field="source quantity",
    )

    unit_cost = _decimal(
        source.unit_cost,
        field="source unit_cost",
    )

    valuation_amount = _decimal(
        source.valuation_amount,
        field="source valuation_amount",
    )

    cost_amount = _decimal(
        source.cost_amount,
        field="source cost_amount",
    )

    if quantity <= ZERO:
        raise SalesReturnCostDataIntegrityError(
            "Source quantity must be greater than zero"
        )

    if unit_cost < ZERO:
        raise SalesReturnCostDataIntegrityError(
            "Source unit_cost cannot be negative"
        )

    if valuation_amount < ZERO:
        raise SalesReturnCostDataIntegrityError(
            "Source valuation_amount cannot be negative"
        )

    if cost_amount < ZERO:
        raise SalesReturnCostDataIntegrityError(
            "Source cost_amount cannot be negative"
        )

    normalized_valuation = _valuation_amount(
        valuation_amount
    )

    normalized_cost = _money(
        cost_amount
    )

    if (
        _money(
            normalized_valuation
        )
        != normalized_cost
    ):
        raise SalesReturnCostDataIntegrityError(
            "InventoryCostEntry cost_amount does not "
            "match valuation_amount currency rounding"
        )

    expected_unit_cost = _unit_cost(
        normalized_valuation
        / quantity
    )

    if (
        _unit_cost(
            unit_cost
        )
        != expected_unit_cost
    ):
        raise SalesReturnCostDataIntegrityError(
            "InventoryCostEntry unit_cost does not match "
            "valuation_amount / quantity"
        )

    return SalesReturnIssueCostSource(
        source_id=source_id,
        issue_date=source.issue_date,
        valuation_method=method,
        quantity=quantity,
        unit_cost=expected_unit_cost,
        valuation_amount=normalized_valuation,
        cost_amount=normalized_cost,
    )


def _normalize_candidates(
    *,
    source: SalesReturnIssueCostSource,
    candidates: Iterable[
        SalesReturnCostCandidate
    ],
) -> tuple[
    SalesReturnCostCandidate,
    ...,
]:
    seen_ids = set()
    normalized = []

    for candidate in tuple(
        candidates
    ):
        if not isinstance(
            candidate,
            SalesReturnCostCandidate,
        ):
            raise SalesReturnCostDataIntegrityError(
                "candidates must contain "
                "SalesReturnCostCandidate values"
            )

        source_id = _positive_id(
            candidate.return_source_id,
            field="return_source_id",
        )

        if source_id in seen_ids:
            raise SalesReturnCostDataIntegrityError(
                "Duplicate return_source_id"
            )

        seen_ids.add(
            source_id
        )

        if not isinstance(
            candidate.event_date,
            date,
        ):
            raise SalesReturnCostDataIntegrityError(
                "Return event_date must be a date"
            )

        if (
            candidate.event_date
            < source.issue_date
        ):
            raise SalesReturnCostChronologyError(
                "Sales Return cannot precede "
                "the original inventory ISSUE"
            )

        quantity = _decimal(
            candidate.quantity,
            field="returned quantity",
        )

        if quantity <= ZERO:
            raise SalesReturnCostDataIntegrityError(
                "Returned quantity must be greater than zero"
            )

        normalized.append(
            SalesReturnCostCandidate(
                return_source_id=source_id,
                event_date=candidate.event_date,
                quantity=quantity,
            )
        )

    normalized.sort(
        key=lambda candidate: (
            candidate.event_date,
            candidate.return_source_id,
        )
    )

    total_returned = sum(
        (
            candidate.quantity
            for candidate in normalized
        ),
        ZERO,
    )

    if total_returned > source.quantity:
        raise SalesReturnCostCapacityError(
            "Returned quantity exceeds original "
            "InventoryCostEntry quantity"
        )

    return tuple(
        normalized
    )


def _normalize_fifo_slices(
    *,
    source: SalesReturnIssueCostSource,
    fifo_slices: Iterable[
        SalesReturnFifoCostSlice
    ],
) -> tuple[
    SalesReturnFifoCostSlice,
    ...,
]:
    values = tuple(
        fifo_slices
    )

    if (
        source.valuation_method
        == WEIGHTED_AVERAGE_MOVING
    ):
        if values:
            raise SalesReturnCostDataIntegrityError(
                "Moving-average source cannot contain "
                "FIFO cost slices"
            )

        return ()

    if not values:
        raise SalesReturnCostDataIntegrityError(
            "FIFO source requires original "
            "StockLotConsumption slices"
        )

    seen_ids = set()
    seen_lot_ids = set()
    normalized = []

    for item in values:
        if not isinstance(
            item,
            SalesReturnFifoCostSlice,
        ):
            raise SalesReturnCostDataIntegrityError(
                "fifo_slices must contain "
                "SalesReturnFifoCostSlice values"
            )

        source_id = _positive_id(
            item.source_id,
            field="FIFO consumption source_id",
        )

        stock_lot_id = _positive_id(
            item.stock_lot_id,
            field="FIFO stock_lot_id",
        )

        if source_id in seen_ids:
            raise SalesReturnCostDataIntegrityError(
                "Duplicate FIFO consumption source_id"
            )

        if stock_lot_id in seen_lot_ids:
            raise SalesReturnCostDataIntegrityError(
                "Duplicate FIFO stock_lot_id"
            )

        seen_ids.add(
            source_id
        )

        seen_lot_ids.add(
            stock_lot_id
        )

        quantity = _decimal(
            item.quantity,
            field="FIFO slice quantity",
        )

        unit_cost = _decimal(
            item.unit_cost,
            field="FIFO slice unit_cost",
        )

        if quantity <= ZERO:
            raise SalesReturnCostDataIntegrityError(
                "FIFO slice quantity must be greater than zero"
            )

        if unit_cost < ZERO:
            raise SalesReturnCostDataIntegrityError(
                "FIFO slice unit_cost cannot be negative"
            )

        normalized.append(
            SalesReturnFifoCostSlice(
                source_id=source_id,
                stock_lot_id=stock_lot_id,
                quantity=quantity,
                unit_cost=_unit_cost(
                    unit_cost
                ),
            )
        )

    normalized.sort(
        key=lambda item: item.source_id
    )

    total_quantity = sum(
        (
            item.quantity
            for item in normalized
        ),
        ZERO,
    )

    if total_quantity != source.quantity:
        raise SalesReturnCostDataIntegrityError(
            "FIFO consumption quantities do not match "
            "InventoryCostEntry quantity"
        )

    raw_total = sum(
        (
            item.quantity
            * item.unit_cost
            for item in normalized
        ),
        ZERO,
    )

    if (
        _valuation_amount(
            raw_total
        )
        != source.valuation_amount
    ):
        raise SalesReturnCostDataIntegrityError(
            "FIFO consumption historical cost does not match "
            "InventoryCostEntry valuation_amount"
        )

    return tuple(
        normalized
    )


def _build_moving_average_targets(
    *,
    source: SalesReturnIssueCostSource,
    candidates: tuple[
        SalesReturnCostCandidate,
        ...,
    ],
) -> tuple[
    SalesReturnCostTarget,
    ...,
]:
    targets = []

    cumulative_quantity = ZERO

    cumulative_valuation = (
        Decimal("0.00000000")
    )

    cumulative_cost = Decimal("0.00")

    for candidate in candidates:
        previous_valuation = (
            cumulative_valuation
        )

        previous_cost = cumulative_cost

        cumulative_quantity += (
            candidate.quantity
        )

        if (
            cumulative_quantity
            == source.quantity
        ):
            cumulative_valuation = (
                source.valuation_amount
            )

            cumulative_cost = (
                source.cost_amount
            )

        else:
            cumulative_valuation = (
                _valuation_amount(
                    source.valuation_amount
                    * cumulative_quantity
                    / source.quantity
                )
            )

            cumulative_cost = _money(
                cumulative_valuation
            )

        restored_valuation = (
            cumulative_valuation
            - previous_valuation
        )

        restored_cost = (
            cumulative_cost
            - previous_cost
        )

        aggregate_unit_cost = _unit_cost(
            restored_valuation
            / candidate.quantity
        )

        targets.append(
            SalesReturnCostTarget(
                return_source_id=(
                    candidate.return_source_id
                ),
                inventory_cost_entry_id=(
                    source.source_id
                ),
                event_date=(
                    candidate.event_date
                ),
                valuation_method=(
                    source.valuation_method
                ),
                restored_quantity=(
                    candidate.quantity
                ),
                restored_valuation_amount=(
                    restored_valuation
                ),
                restored_cost_amount=(
                    restored_cost
                ),
                aggregate_historical_unit_cost=(
                    aggregate_unit_cost
                ),
                fifo_slices=(),
            )
        )

    return tuple(
        targets
    )


def _build_fifo_targets(
    *,
    source: SalesReturnIssueCostSource,
    candidates: tuple[
        SalesReturnCostCandidate,
        ...,
    ],
    fifo_slices: tuple[
        SalesReturnFifoCostSlice,
        ...,
    ],
) -> tuple[
    SalesReturnCostTarget,
    ...,
]:
    """
    Restore marginal FIFO consumption in reverse consumption order.

    Original ISSUE consumes FIFO:
        oldest lot -> newer lot

    Partial Sales Return restores:
        newer consumed slice -> older consumed slice

    This preserves the remaining ISSUE cost as the cost that would
    have resulted if the original FIFO ISSUE quantity had been lower.
    """

    restoration_order = list(
        reversed(
            fifo_slices
        )
    )

    remaining_by_consumption = {
        item.source_id: item.quantity
        for item in restoration_order
    }

    slice_index = 0

    cumulative_quantity = ZERO
    cumulative_raw_valuation = ZERO

    cumulative_valuation = (
        Decimal("0.00000000")
    )

    cumulative_cost = Decimal("0.00")

    targets = []

    for candidate in candidates:
        previous_candidate_valuation = (
            cumulative_valuation
        )

        previous_candidate_cost = (
            cumulative_cost
        )

        quantity_to_restore = (
            candidate.quantity
        )

        candidate_slice_targets = []

        while quantity_to_restore > ZERO:
            if (
                slice_index
                >= len(
                    restoration_order
                )
            ):
                raise SalesReturnCostDataIntegrityError(
                    "FIFO restoration exhausted "
                    "historical consumption slices"
                )

            source_slice = (
                restoration_order[
                    slice_index
                ]
            )

            available = (
                remaining_by_consumption[
                    source_slice.source_id
                ]
            )

            if available <= ZERO:
                slice_index += 1
                continue

            restored_quantity = min(
                available,
                quantity_to_restore,
            )

            previous_slice_valuation = (
                cumulative_valuation
            )

            cumulative_quantity += (
                restored_quantity
            )

            cumulative_raw_valuation += (
                restored_quantity
                * source_slice.unit_cost
            )

            if (
                cumulative_quantity
                == source.quantity
            ):
                cumulative_valuation = (
                    source.valuation_amount
                )
            else:
                cumulative_valuation = (
                    _valuation_amount(
                        cumulative_raw_valuation
                    )
                )

            restored_slice_valuation = (
                cumulative_valuation
                - previous_slice_valuation
            )

            candidate_slice_targets.append(
                SalesReturnFifoSliceTarget(
                    fifo_consumption_id=(
                        source_slice.source_id
                    ),
                    stock_lot_id=(
                        source_slice.stock_lot_id
                    ),
                    quantity=(
                        restored_quantity
                    ),
                    unit_cost=(
                        source_slice.unit_cost
                    ),
                    valuation_amount=(
                        restored_slice_valuation
                    ),
                )
            )

            remaining_by_consumption[
                source_slice.source_id
            ] = (
                available
                - restored_quantity
            )

            quantity_to_restore -= (
                restored_quantity
            )

            if (
                remaining_by_consumption[
                    source_slice.source_id
                ]
                == ZERO
            ):
                slice_index += 1

        if (
            cumulative_quantity
            == source.quantity
        ):
            cumulative_cost = (
                source.cost_amount
            )
        else:
            cumulative_cost = _money(
                cumulative_valuation
            )

        restored_valuation = (
            cumulative_valuation
            - previous_candidate_valuation
        )

        restored_cost = (
            cumulative_cost
            - previous_candidate_cost
        )

        aggregate_unit_cost = _unit_cost(
            restored_valuation
            / candidate.quantity
        )

        targets.append(
            SalesReturnCostTarget(
                return_source_id=(
                    candidate.return_source_id
                ),
                inventory_cost_entry_id=(
                    source.source_id
                ),
                event_date=(
                    candidate.event_date
                ),
                valuation_method=(
                    source.valuation_method
                ),
                restored_quantity=(
                    candidate.quantity
                ),
                restored_valuation_amount=(
                    restored_valuation
                ),
                restored_cost_amount=(
                    restored_cost
                ),
                aggregate_historical_unit_cost=(
                    aggregate_unit_cost
                ),
                fifo_slices=tuple(
                    candidate_slice_targets
                ),
            )
        )

    return tuple(
        targets
    )


def build_sales_return_cost_targets(
    *,
    source: SalesReturnIssueCostSource,
    candidates: Iterable[
        SalesReturnCostCandidate
    ],
    fifo_slices: Iterable[
        SalesReturnFifoCostSlice
    ] = (),
) -> tuple[
    SalesReturnCostTarget,
    ...,
]:
    """
    Pure historical-cost restoration.

    No DB.
    No StockBalance mutation.
    No StockLot creation.
    No MovingAverageBalance mutation.
    No JournalEntry.
    No VAT / RK.

    Moving average:
        restore the historical ISSUE cost proportionally from the
        immutable InventoryCostEntry.

    FIFO:
        restore exact original StockLotConsumption provenance in
        reverse consumption order.

    Monetary allocation uses cumulative rounding so multiple partial
    returns conserve the original InventoryCostEntry.cost_amount.
    """

    normalized_source = (
        _normalize_source(
            source
        )
    )

    normalized_candidates = (
        _normalize_candidates(
            source=normalized_source,
            candidates=candidates,
        )
    )

    normalized_fifo_slices = (
        _normalize_fifo_slices(
            source=normalized_source,
            fifo_slices=fifo_slices,
        )
    )

    if not normalized_candidates:
        return ()

    if (
        normalized_source.valuation_method
        == FIFO
    ):
        return _build_fifo_targets(
            source=normalized_source,
            candidates=normalized_candidates,
            fifo_slices=normalized_fifo_slices,
        )

    if (
        normalized_source.valuation_method
        == WEIGHTED_AVERAGE_MOVING
    ):
        return _build_moving_average_targets(
            source=normalized_source,
            candidates=normalized_candidates,
        )

    raise SalesReturnCostMethodError(
        "Unsupported inventory valuation method"
    )
