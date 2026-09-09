from dataclasses import dataclass
from datetime import (
    date,
    datetime,
)
from decimal import (
    Decimal,
    InvalidOperation,
)
from typing import Literal

from app.services.money_rounding import (
    round_currency_amount,
)


ZERO = Decimal("0")

PurchaseValueCorrectionFifoDestinationKind = Literal[
    "issued",
    "on_hand",
]


class PurchaseValueCorrectionFifoImpactCalculationError(
    Exception
):
    """Base pure FIFO impact calculation error."""


class PurchaseValueCorrectionFifoImpactSourceError(
    PurchaseValueCorrectionFifoImpactCalculationError
):
    """FIFO impact source identity is invalid."""


class PurchaseValueCorrectionFifoImpactQuantityError(
    PurchaseValueCorrectionFifoImpactCalculationError
):
    """FIFO quantity attribution is inconsistent."""


class PurchaseValueCorrectionFifoImpactAmountError(
    PurchaseValueCorrectionFifoImpactCalculationError
):
    """FIFO monetary attribution is inconsistent."""


class PurchaseValueCorrectionFifoImpactCurrencyError(
    PurchaseValueCorrectionFifoImpactCalculationError
):
    """FIFO monetary sources use inconsistent currencies."""


@dataclass(
    frozen=True,
    slots=True,
)
class ActiveFifoAllocationPeerCandidate:
    invoice_fulfillment_allocation_id: int
    receipt_event_date: date
    quantity: Decimal


@dataclass(
    frozen=True,
    slots=True,
)
class PurchaseValueCorrectionFifoAllocationCandidate:
    purchase_value_correction_allocation_event_id: int
    invoice_fulfillment_allocation_id: int
    recognition_date: date
    quantity: Decimal
    original_allocated_base_amount: Decimal
    corrected_allocated_base_amount: Decimal
    currency_code: str


@dataclass(
    frozen=True,
    slots=True,
)
class ActiveFifoConsumptionCandidate:
    stock_lot_consumption_id: int
    issue_document_id: int
    issue_document_line_id: int
    issue_event_date: date
    quantity: Decimal


@dataclass(
    frozen=True,
    slots=True,
)
class PurchaseValueCorrectionFifoImpactTarget:
    purchase_value_correction_allocation_event_id: int
    invoice_fulfillment_allocation_id: int
    stock_lot_id: int

    destination_kind: (
        PurchaseValueCorrectionFifoDestinationKind
    )

    stock_lot_consumption_id: int | None
    issue_document_id: int | None
    issue_document_line_id: int | None

    quantity: Decimal
    recognition_date: date

    original_base_amount: Decimal
    corrected_base_amount: Decimal

    currency_code: str

    @property
    def base_amount_delta(
        self,
    ) -> Decimal:
        return (
            self.corrected_base_amount
            - self.original_base_amount
        )

    @property
    def is_noop(
        self,
    ) -> bool:
        return (
            self.original_base_amount
            == self.corrected_base_amount
        )


@dataclass(
    frozen=True,
    slots=True,
)
class _AllocationInterval:
    candidate: ActiveFifoAllocationPeerCandidate
    start: Decimal
    end: Decimal


@dataclass(
    frozen=True,
    slots=True,
)
class _ConsumptionInterval:
    candidate: ActiveFifoConsumptionCandidate
    start: Decimal
    end: Decimal


@dataclass(
    frozen=True,
    slots=True,
)
class _DestinationSlice:
    destination_kind: (
        PurchaseValueCorrectionFifoDestinationKind
    )
    stock_lot_consumption_id: int | None
    issue_document_id: int | None
    issue_document_line_id: int | None
    quantity: Decimal
    recognition_date: date


def _positive_id(
    value: int,
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
        raise PurchaseValueCorrectionFifoImpactSourceError(
            f"{field} must be a positive integer"
        )

    return value


def _business_date(
    value: date,
    *,
    field: str,
) -> date:
    if (
        not isinstance(
            value,
            date,
        )
        or isinstance(
            value,
            datetime,
        )
    ):
        raise PurchaseValueCorrectionFifoImpactSourceError(
            f"{field} must be a date"
        )

    return value


def _decimal(
    value,
    *,
    field: str,
) -> Decimal:
    if isinstance(
        value,
        bool,
    ):
        raise PurchaseValueCorrectionFifoImpactQuantityError(
            f"{field} must be numeric"
        )

    try:
        result = Decimal(
            value
        )
    except (
        InvalidOperation,
        TypeError,
        ValueError,
    ) as exc:
        raise PurchaseValueCorrectionFifoImpactQuantityError(
            f"{field} must be numeric"
        ) from exc

    if not result.is_finite():
        raise PurchaseValueCorrectionFifoImpactQuantityError(
            f"{field} must be finite"
        )

    return result


def _money(
    value,
    *,
    currency_code: str,
    field: str,
) -> Decimal:
    try:
        amount = Decimal(
            value
        )
    except (
        InvalidOperation,
        TypeError,
        ValueError,
    ) as exc:
        raise PurchaseValueCorrectionFifoImpactAmountError(
            f"{field} must be numeric"
        ) from exc

    if not amount.is_finite():
        raise PurchaseValueCorrectionFifoImpactAmountError(
            f"{field} must be finite"
        )

    try:
        return round_currency_amount(
            amount=amount,
            currency_code=currency_code,
        )
    except Exception as exc:
        raise PurchaseValueCorrectionFifoImpactAmountError(
            f"{field} cannot be rounded"
        ) from exc


def _currency(
    value: str,
) -> str:
    if (
        not isinstance(
            value,
            str,
        )
        or len(value) != 3
    ):
        raise PurchaseValueCorrectionFifoImpactCurrencyError(
            "currency_code must contain exactly "
            "3 characters"
        )

    return value.upper()


def _intersection_quantity(
    *,
    left_start: Decimal,
    left_end: Decimal,
    right_start: Decimal,
    right_end: Decimal,
) -> Decimal:
    start = max(
        left_start,
        right_start,
    )

    end = min(
        left_end,
        right_end,
    )

    if end <= start:
        return ZERO

    return (
        end
        - start
    )


def _cumulative_amount(
    *,
    total_amount: Decimal,
    cumulative_quantity: Decimal,
    total_quantity: Decimal,
    currency_code: str,
) -> Decimal:
    if cumulative_quantity == ZERO:
        return _money(
            ZERO,
            currency_code=currency_code,
            field="cumulative amount",
        )

    if cumulative_quantity == total_quantity:
        return total_amount

    return _money(
        (
            total_amount
            * cumulative_quantity
            / total_quantity
        ),
        currency_code=currency_code,
        field="cumulative amount",
    )


def _build_allocation_intervals(
    *,
    receipt_quantity: Decimal,
    candidates: tuple[
        ActiveFifoAllocationPeerCandidate,
        ...,
    ],
) -> tuple[
    _AllocationInterval,
    ...,
]:
    """
    Build physical quantity ownership from ALL economically
    ACTIVE InvoiceFulfillmentAllocation peers on one receipt
    fulfillment line.

    Monetary correction sources are deliberately not used to
    construct these intervals. An uncorrected IFA must still
    reserve its exact quantity interval inside the StockLot.
    """

    normalized = []
    allocation_ids = set()

    for candidate in candidates:
        if not isinstance(
            candidate,
            ActiveFifoAllocationPeerCandidate,
        ):
            raise PurchaseValueCorrectionFifoImpactSourceError(
                "ACTIVE allocation peer has invalid type"
            )

        allocation_id = _positive_id(
            candidate.invoice_fulfillment_allocation_id,
            field="invoice_fulfillment_allocation_id",
        )

        if allocation_id in allocation_ids:
            raise PurchaseValueCorrectionFifoImpactSourceError(
                "InvoiceFulfillmentAllocation peer IDs "
                "must be unique"
            )

        allocation_ids.add(
            allocation_id
        )

        receipt_event_date = _business_date(
            candidate.receipt_event_date,
            field="receipt_event_date",
        )

        quantity = _decimal(
            candidate.quantity,
            field="ACTIVE allocation peer quantity",
        )

        if quantity <= ZERO:
            raise PurchaseValueCorrectionFifoImpactQuantityError(
                "ACTIVE allocation peer quantity "
                "must be positive"
            )

        normalized.append(
            ActiveFifoAllocationPeerCandidate(
                invoice_fulfillment_allocation_id=(
                    allocation_id
                ),
                receipt_event_date=(
                    receipt_event_date
                ),
                quantity=quantity,
            )
        )

    ordered = tuple(
        sorted(
            normalized,
            key=lambda item: (
                item.receipt_event_date,
                item.invoice_fulfillment_allocation_id,
            ),
        )
    )

    allocated_quantity = sum(
        (
            candidate.quantity
            for candidate in ordered
        ),
        ZERO,
    )

    if allocated_quantity > receipt_quantity:
        raise PurchaseValueCorrectionFifoImpactQuantityError(
            "ACTIVE InvoiceFulfillmentAllocation peer "
            "quantity exceeds receipt quantity"
        )

    intervals = []
    cursor = ZERO

    for candidate in ordered:
        end = (
            cursor
            + candidate.quantity
        )

        intervals.append(
            _AllocationInterval(
                candidate=candidate,
                start=cursor,
                end=end,
            )
        )

        cursor = end

    return tuple(
        intervals
    )


def _normalize_correction_sources(
    *,
    candidates: tuple[
        PurchaseValueCorrectionFifoAllocationCandidate,
        ...,
    ],
    intervals_by_ifa: dict[
        int,
        _AllocationInterval,
    ],
) -> tuple[
    PurchaseValueCorrectionFifoAllocationCandidate,
    ...,
]:
    normalized = []

    event_ids = set()
    allocation_ids = set()
    currency = None

    for candidate in candidates:
        if not isinstance(
            candidate,
            PurchaseValueCorrectionFifoAllocationCandidate,
        ):
            raise PurchaseValueCorrectionFifoImpactSourceError(
                "correction allocation candidate "
                "has invalid type"
            )

        event_id = _positive_id(
            candidate
            .purchase_value_correction_allocation_event_id,
            field=(
                "purchase_value_correction_"
                "allocation_event_id"
            ),
        )

        allocation_id = _positive_id(
            candidate
            .invoice_fulfillment_allocation_id,
            field="invoice_fulfillment_allocation_id",
        )

        if event_id in event_ids:
            raise PurchaseValueCorrectionFifoImpactSourceError(
                "Purchase Value Correction allocation "
                "event IDs must be unique"
            )

        if allocation_id in allocation_ids:
            raise PurchaseValueCorrectionFifoImpactSourceError(
                "InvoiceFulfillmentAllocation IDs "
                "must be unique among correction sources"
            )

        event_ids.add(
            event_id
        )

        allocation_ids.add(
            allocation_id
        )

        interval = intervals_by_ifa.get(
            allocation_id
        )

        if interval is None:
            raise PurchaseValueCorrectionFifoImpactSourceError(
                "Correction source references an IFA "
                "that is not present in ACTIVE peer set"
            )

        recognition_date = _business_date(
            candidate.recognition_date,
            field="correction recognition_date",
        )

        quantity = _decimal(
            candidate.quantity,
            field="correction allocation quantity",
        )

        if quantity <= ZERO:
            raise PurchaseValueCorrectionFifoImpactQuantityError(
                "correction allocation quantity "
                "must be positive"
            )

        if (
            quantity
            != interval.candidate.quantity
        ):
            raise PurchaseValueCorrectionFifoImpactQuantityError(
                "Correction allocation quantity does not "
                "match ACTIVE IFA peer quantity"
            )

        candidate_currency = _currency(
            candidate.currency_code
        )

        if currency is None:
            currency = candidate_currency
        elif candidate_currency != currency:
            raise PurchaseValueCorrectionFifoImpactCurrencyError(
                "correction sources must use one currency"
            )

        original_amount = _money(
            candidate.original_allocated_base_amount,
            currency_code=candidate_currency,
            field="original allocated base",
        )

        corrected_amount = _money(
            candidate.corrected_allocated_base_amount,
            currency_code=candidate_currency,
            field="corrected allocated base",
        )

        if (
            original_amount < ZERO
            or corrected_amount < ZERO
        ):
            raise PurchaseValueCorrectionFifoImpactAmountError(
                "allocated base amounts cannot be negative"
            )

        if original_amount == corrected_amount:
            raise PurchaseValueCorrectionFifoImpactAmountError(
                "active Purchase Value Correction "
                "allocation source cannot be a no-op"
            )

        normalized.append(
            PurchaseValueCorrectionFifoAllocationCandidate(
                purchase_value_correction_allocation_event_id=(
                    event_id
                ),
                invoice_fulfillment_allocation_id=(
                    allocation_id
                ),
                recognition_date=(
                    recognition_date
                ),
                quantity=quantity,
                original_allocated_base_amount=(
                    original_amount
                ),
                corrected_allocated_base_amount=(
                    corrected_amount
                ),
                currency_code=candidate_currency,
            )
        )

    return tuple(
        sorted(
            normalized,
            key=lambda item: (
                intervals_by_ifa[
                    item.invoice_fulfillment_allocation_id
                ].start,
                item.purchase_value_correction_allocation_event_id,
            ),
        )
    )


def _build_consumption_intervals(
    *,
    current_consumed_quantity: Decimal,
    candidates: tuple[
        ActiveFifoConsumptionCandidate,
        ...,
    ],
) -> tuple[
    _ConsumptionInterval,
    ...,
]:
    normalized = []
    consumption_ids = set()

    for candidate in candidates:
        if not isinstance(
            candidate,
            ActiveFifoConsumptionCandidate,
        ):
            raise PurchaseValueCorrectionFifoImpactSourceError(
                "FIFO consumption candidate "
                "has invalid type"
            )

        consumption_id = _positive_id(
            candidate.stock_lot_consumption_id,
            field="stock_lot_consumption_id",
        )

        if consumption_id in consumption_ids:
            raise PurchaseValueCorrectionFifoImpactSourceError(
                "StockLotConsumption IDs "
                "must be unique"
            )

        consumption_ids.add(
            consumption_id
        )

        issue_document_id = _positive_id(
            candidate.issue_document_id,
            field="issue_document_id",
        )

        issue_document_line_id = _positive_id(
            candidate.issue_document_line_id,
            field="issue_document_line_id",
        )

        issue_event_date = _business_date(
            candidate.issue_event_date,
            field="issue_event_date",
        )

        quantity = _decimal(
            candidate.quantity,
            field="consumption quantity",
        )

        if quantity <= ZERO:
            raise PurchaseValueCorrectionFifoImpactQuantityError(
                "active FIFO consumption quantity "
                "must be positive"
            )

        normalized.append(
            ActiveFifoConsumptionCandidate(
                stock_lot_consumption_id=(
                    consumption_id
                ),
                issue_document_id=(
                    issue_document_id
                ),
                issue_document_line_id=(
                    issue_document_line_id
                ),
                issue_event_date=(
                    issue_event_date
                ),
                quantity=quantity,
            )
        )

    ordered = tuple(
        sorted(
            normalized,
            key=lambda item: (
                item.issue_event_date,
                item.issue_document_id,
                item.issue_document_line_id,
                item.stock_lot_consumption_id,
            ),
        )
    )

    total = sum(
        (
            candidate.quantity
            for candidate in ordered
        ),
        ZERO,
    )

    if total != current_consumed_quantity:
        raise PurchaseValueCorrectionFifoImpactQuantityError(
            "ACTIVE FIFO consumption quantity "
            "does not equal StockLot current "
            "consumed quantity"
        )

    intervals = []
    cursor = ZERO

    for candidate in ordered:
        end = (
            cursor
            + candidate.quantity
        )

        intervals.append(
            _ConsumptionInterval(
                candidate=candidate,
                start=cursor,
                end=end,
            )
        )

        cursor = end

    return tuple(
        intervals
    )


def _build_destination_slices(
    *,
    allocation: _AllocationInterval,
    source_recognition_date: date,
    consumptions: tuple[
        _ConsumptionInterval,
        ...,
    ],
    current_consumed_quantity: Decimal,
    receipt_quantity: Decimal,
) -> tuple[
    _DestinationSlice,
    ...,
]:
    slices = []

    for consumption in consumptions:
        quantity = _intersection_quantity(
            left_start=allocation.start,
            left_end=allocation.end,
            right_start=consumption.start,
            right_end=consumption.end,
        )

        if quantity == ZERO:
            continue

        slices.append(
            _DestinationSlice(
                destination_kind="issued",
                stock_lot_consumption_id=(
                    consumption
                    .candidate
                    .stock_lot_consumption_id
                ),
                issue_document_id=(
                    consumption
                    .candidate
                    .issue_document_id
                ),
                issue_document_line_id=(
                    consumption
                    .candidate
                    .issue_document_line_id
                ),
                quantity=quantity,
                recognition_date=max(
                    source_recognition_date,
                    consumption.candidate.issue_event_date,
                ),
            )
        )

    on_hand_quantity = _intersection_quantity(
        left_start=allocation.start,
        left_end=allocation.end,
        right_start=current_consumed_quantity,
        right_end=receipt_quantity,
    )

    if on_hand_quantity > ZERO:
        slices.append(
            _DestinationSlice(
                destination_kind="on_hand",
                stock_lot_consumption_id=None,
                issue_document_id=None,
                issue_document_line_id=None,
                quantity=on_hand_quantity,
                recognition_date=source_recognition_date,
            )
        )

    total = sum(
        (
            item.quantity
            for item in slices
        ),
        ZERO,
    )

    if (
        total
        != allocation.candidate.quantity
    ):
        raise PurchaseValueCorrectionFifoImpactQuantityError(
            "FIFO destination slices do not "
            "conserve corrected IFA quantity"
        )

    return tuple(
        slices
    )


def _allocate_source_amounts(
    *,
    stock_lot_id: int,
    source: PurchaseValueCorrectionFifoAllocationCandidate,
    slices: tuple[
        _DestinationSlice,
        ...,
    ],
) -> tuple[
    PurchaseValueCorrectionFifoImpactTarget,
    ...,
]:
    quantity = source.quantity

    original_total = (
        source.original_allocated_base_amount
    )

    corrected_total = (
        source.corrected_allocated_base_amount
    )

    currency = source.currency_code

    cumulative_quantity = ZERO

    original_before = _money(
        ZERO,
        currency_code=currency,
        field="original cumulative amount",
    )

    corrected_before = _money(
        ZERO,
        currency_code=currency,
        field="corrected cumulative amount",
    )

    targets = []

    for item in slices:
        cumulative_after = (
            cumulative_quantity
            + item.quantity
        )

        original_after = _cumulative_amount(
            total_amount=original_total,
            cumulative_quantity=cumulative_after,
            total_quantity=quantity,
            currency_code=currency,
        )

        corrected_after = _cumulative_amount(
            total_amount=corrected_total,
            cumulative_quantity=cumulative_after,
            total_quantity=quantity,
            currency_code=currency,
        )

        target = (
            PurchaseValueCorrectionFifoImpactTarget(
                purchase_value_correction_allocation_event_id=(
                    source
                    .purchase_value_correction_allocation_event_id
                ),
                invoice_fulfillment_allocation_id=(
                    source.invoice_fulfillment_allocation_id
                ),
                stock_lot_id=stock_lot_id,
                destination_kind=(
                    item.destination_kind
                ),
                stock_lot_consumption_id=(
                    item.stock_lot_consumption_id
                ),
                issue_document_id=(
                    item.issue_document_id
                ),
                issue_document_line_id=(
                    item.issue_document_line_id
                ),
                quantity=item.quantity,
                recognition_date=item.recognition_date,
                original_base_amount=(
                    original_after
                    - original_before
                ),
                corrected_base_amount=(
                    corrected_after
                    - corrected_before
                ),
                currency_code=currency,
            )
        )

        if (
            target.original_base_amount < ZERO
            or target.corrected_base_amount < ZERO
        ):
            raise PurchaseValueCorrectionFifoImpactAmountError(
                "FIFO destination amount "
                "cannot be negative"
            )

        targets.append(
            target
        )

        cumulative_quantity = (
            cumulative_after
        )

        original_before = (
            original_after
        )

        corrected_before = (
            corrected_after
        )

    if cumulative_quantity != quantity:
        raise PurchaseValueCorrectionFifoImpactQuantityError(
            "FIFO monetary slices do not "
            "conserve source quantity"
        )

    if (
        sum(
            (
                item.original_base_amount
                for item in targets
            ),
            ZERO,
        )
        != original_total
    ):
        raise PurchaseValueCorrectionFifoImpactAmountError(
            "FIFO original-base allocation "
            "does not close to source amount"
        )

    if (
        sum(
            (
                item.corrected_base_amount
                for item in targets
            ),
            ZERO,
        )
        != corrected_total
    ):
        raise PurchaseValueCorrectionFifoImpactAmountError(
            "FIFO corrected-base allocation "
            "does not close to source amount"
        )

    return tuple(
        targets
    )


def build_purchase_value_correction_fifo_impact_targets(
    *,
    stock_lot_id: int,
    receipt_quantity: Decimal,
    current_consumed_quantity: Decimal,
    active_allocation_peers: tuple[
        ActiveFifoAllocationPeerCandidate,
        ...,
    ],
    allocation_candidates: tuple[
        PurchaseValueCorrectionFifoAllocationCandidate,
        ...,
    ],
    active_consumptions: tuple[
        ActiveFifoConsumptionCandidate,
        ...,
    ],
) -> tuple[
    PurchaseValueCorrectionFifoImpactTarget,
    ...,
]:
    """
    Pure FIFO attribution for Purchase Value Correction.

    Physical ownership and monetary correction sources are
    intentionally separate.

    Physical quantity policy:

        ALL economically ACTIVE IFA peers on one fulfillment
        receipt line reserve deterministic StockLot intervals in:

            receipt_event_date
            -> InvoiceFulfillmentAllocation.id

        order.

        This includes peers with no price correction. Therefore
        an uncorrected IFA still occupies its physical quantity
        range and cannot be silently compressed out of the lot.

        economically ACTIVE FIFO consumptions are laid out in:

            issue_event_date
            -> issue_document_id
            -> issue_document_line_id
            -> StockLotConsumption.id

        order.

        StockLot current consumed quantity is authoritative and
        must equal the sum of supplied ACTIVE consumptions.

    Monetary policy:

        only immutable PurchaseValueCorrectionAllocationEvent
        sources carry before/after monetary amounts.

        each correction source must match exactly one ACTIVE IFA
        peer and must use that peer's full quantity interval.

        before and after amounts are allocated independently
        across issued/on-hand slices using cumulative-delta
        currency rounding.

    No SQLAlchemy, DB access, JournalEntry, 631, VAT, inventory
    mutation, commit, or rollback belongs in this layer.
    """

    stock_lot_id = _positive_id(
        stock_lot_id,
        field="stock_lot_id",
    )

    receipt_quantity = _decimal(
        receipt_quantity,
        field="receipt_quantity",
    )

    current_consumed_quantity = _decimal(
        current_consumed_quantity,
        field="current_consumed_quantity",
    )

    if receipt_quantity <= ZERO:
        raise PurchaseValueCorrectionFifoImpactQuantityError(
            "receipt_quantity must be positive"
        )

    if current_consumed_quantity < ZERO:
        raise PurchaseValueCorrectionFifoImpactQuantityError(
            "current_consumed_quantity "
            "cannot be negative"
        )

    if current_consumed_quantity > receipt_quantity:
        raise PurchaseValueCorrectionFifoImpactQuantityError(
            "current consumed quantity "
            "cannot exceed receipt quantity"
        )

    intervals = _build_allocation_intervals(
        receipt_quantity=receipt_quantity,
        candidates=tuple(
            active_allocation_peers
        ),
    )

    intervals_by_ifa = {
        interval
        .candidate
        .invoice_fulfillment_allocation_id: interval
        for interval in intervals
    }

    correction_sources = _normalize_correction_sources(
        candidates=tuple(
            allocation_candidates
        ),
        intervals_by_ifa=intervals_by_ifa,
    )

    consumptions = _build_consumption_intervals(
        current_consumed_quantity=(
            current_consumed_quantity
        ),
        candidates=tuple(
            active_consumptions
        ),
    )

    targets = []

    for source in correction_sources:
        allocation = intervals_by_ifa[
            source.invoice_fulfillment_allocation_id
        ]

        slices = _build_destination_slices(
            allocation=allocation,
            source_recognition_date=(
                source.recognition_date
            ),
            consumptions=consumptions,
            current_consumed_quantity=(
                current_consumed_quantity
            ),
            receipt_quantity=receipt_quantity,
        )

        targets.extend(
            _allocate_source_amounts(
                stock_lot_id=stock_lot_id,
                source=source,
                slices=slices,
            )
        )

    return tuple(
        targets
    )
