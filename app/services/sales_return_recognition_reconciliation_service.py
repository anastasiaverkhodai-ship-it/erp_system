from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Iterable

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.invoice_fulfillment_allocation import (
    InvoiceFulfillmentAllocation,
)
from app.models.sales_recognition_event import (
    SalesRecognitionEvent,
)
from app.models.sales_return_recognition_event import (
    SalesReturnRecognitionEvent,
)
from app.models.trade_return_event import (
    TradeReturnEvent,
)
from app.services.sales_return_recognition_persistence_service import (
    SalesReturnRecognitionDataIntegrityError,
    build_current_sales_return_recognition_targets,
    reconcile_sales_return_recognition_source,
)
from app.services.trade_return_calculation_service import (
    TradeReturnCalculationError,
    TradeReturnCandidate,
    TradeReturnEconomicCapacity,
    TradeReturnTarget,
    build_trade_return_targets,
)


ZERO = Decimal("0")


class SalesReturnRecognitionReconciliationError(
    Exception
):
    """Base complete Sales Return reconciliation error."""


class SalesReturnRecognitionReconciliationDataIntegrityError(
    SalesReturnRecognitionReconciliationError
):
    """Sales Return reconciliation sources are inconsistent."""


@dataclass(
    frozen=True,
    slots=True,
)
class SalesReturnRecognitionCapacitySource:
    """
    Active Sales economic capacity.

    allocation_id is used only as deterministic FIFO identity.

    sales_recognition_event_id is the exact immutable economic
    event provenance persisted by SalesReturnRecognitionEvent.
    """

    allocation_id: int
    sales_recognition_event_id: int
    event_date: date
    quantity: Decimal
    gross_amount: Decimal
    tax_amount: Decimal
    currency_code: str


@dataclass(
    frozen=True,
    slots=True,
)
class SalesReturnRecognitionReconciliationResult:
    fulfillment_id: int
    fulfillment_line_id: int
    currency_code: str | None
    return_candidates: tuple[
        TradeReturnCandidate,
        ...,
    ]
    capacity_sources: tuple[
        SalesReturnRecognitionCapacitySource,
        ...,
    ]
    current_targets: tuple[
        TradeReturnTarget,
        ...,
    ]
    desired_targets: tuple[
        TradeReturnTarget,
        ...,
    ]
    reconciliation_targets: tuple[
        TradeReturnTarget,
        ...,
    ]
    created_events: tuple[
        SalesReturnRecognitionEvent,
        ...,
    ]


def _decimal(
    value,
) -> Decimal:
    return Decimal(
        str(
            value
        )
    )


def _positive_int(
    value: int,
    *,
    label: str,
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
        raise (
            SalesReturnRecognitionReconciliationDataIntegrityError(
                f"{label} must be greater than zero"
            )
        )

    return value


def _currency(
    value: str,
) -> str:
    if not isinstance(
        value,
        str,
    ):
        raise (
            SalesReturnRecognitionReconciliationDataIntegrityError(
                "currency_code must be a string"
            )
        )

    normalized = value.strip().upper()

    if (
        len(
            normalized
        )
        != 3
        or not normalized.isalpha()
    ):
        raise (
            SalesReturnRecognitionReconciliationDataIntegrityError(
                "currency_code must contain exactly "
                "three alphabetic characters"
            )
        )

    return normalized


def _enum_value(
    value,
) -> str:
    return str(
        getattr(
            value,
            "value",
            value,
        )
    ).strip().lower()


def _active_original_rows(
    rows: Iterable,
    *,
    label: str,
) -> tuple:
    row_tuple = tuple(
        rows
    )

    reversed_ids = {
        row.reversal_of_id
        for row in row_tuple
        if getattr(
            row,
            "reversal_of_id",
            None,
        )
        is not None
    }

    active = []

    for row in row_tuple:
        if (
            getattr(
                row,
                "reversal_of_id",
                None,
            )
            is not None
        ):
            continue

        row_id = getattr(
            row,
            "id",
            None,
        )

        if row_id is None:
            raise (
                SalesReturnRecognitionReconciliationDataIntegrityError(
                    f"Persistent original {label} "
                    "has no ID"
                )
            )

        if row_id in reversed_ids:
            continue

        active.append(
            row
        )

    return tuple(
        active
    )


def _target_map(
    targets: Iterable[
        TradeReturnTarget
    ],
    *,
    label: str,
) -> dict[
    tuple[int, int],
    TradeReturnTarget,
]:
    result = {}

    for target in targets:
        if not isinstance(
            target,
            TradeReturnTarget,
        ):
            raise (
                SalesReturnRecognitionReconciliationDataIntegrityError(
                    f"{label} contains invalid target type"
                )
            )

        pair = (
            target.return_source_id,
            target.economic_source_id,
        )

        if pair in result:
            raise (
                SalesReturnRecognitionReconciliationDataIntegrityError(
                    f"{label} contains duplicate source pair"
                )
            )

        result[
            pair
        ] = target

    return result


def _zero_target_from(
    target: TradeReturnTarget,
) -> TradeReturnTarget:
    return TradeReturnTarget(
        return_source_id=(
            target.return_source_id
        ),
        economic_source_id=(
            target.economic_source_id
        ),
        event_date=(
            target.event_date
        ),
        quantity=ZERO,
        gross_amount=ZERO,
        tax_amount=ZERO,
        currency_code=(
            target.currency_code
        ),
    )


def _is_component_decrease(
    *,
    current: TradeReturnTarget,
    desired: TradeReturnTarget,
) -> bool:
    return (
        _decimal(
            desired.quantity
        )
        < _decimal(
            current.quantity
        )
        or _decimal(
            desired.gross_amount
        )
        < _decimal(
            current.gross_amount
        )
        or _decimal(
            desired.tax_amount
        )
        < _decimal(
            current.tax_amount
        )
    )


def build_sales_return_recognition_reconciliation_targets(
    *,
    desired_targets: Iterable[
        TradeReturnTarget
    ],
    current_targets: Iterable[
        TradeReturnTarget
    ],
) -> tuple[
    TradeReturnTarget,
    ...,
]:
    """
    Order pair state changes:

        removals / decreases
        BEFORE
        additions / increases

    Exact matches are omitted.

    Pair provenance date and currency cannot change.
    """

    desired = _target_map(
        desired_targets,
        label="desired_targets",
    )

    current = _target_map(
        current_targets,
        label="current_targets",
    )

    decreases = []
    increases = []

    all_pairs = (
        set(
            desired
        )
        | set(
            current
        )
    )

    for pair in all_pairs:
        wanted = desired.get(
            pair
        )

        existing = current.get(
            pair
        )

        if (
            wanted is not None
            and existing is not None
        ):
            if (
                wanted.event_date
                != existing.event_date
            ):
                raise (
                    SalesReturnRecognitionReconciliationDataIntegrityError(
                        "Existing and desired Sales Return "
                        "pair date differs"
                    )
                )

            if (
                _currency(
                    wanted.currency_code
                )
                != _currency(
                    existing.currency_code
                )
            ):
                raise (
                    SalesReturnRecognitionReconciliationDataIntegrityError(
                        "Existing and desired Sales Return "
                        "pair currency differs"
                    )
                )

            exact = (
                _decimal(
                    wanted.quantity
                )
                == _decimal(
                    existing.quantity
                )
                and _decimal(
                    wanted.gross_amount
                )
                == _decimal(
                    existing.gross_amount
                )
                and _decimal(
                    wanted.tax_amount
                )
                == _decimal(
                    existing.tax_amount
                )
            )

            if exact:
                continue

            if _is_component_decrease(
                current=existing,
                desired=wanted,
            ):
                decreases.append(
                    wanted
                )
            else:
                increases.append(
                    wanted
                )

            continue

        if (
            existing is not None
            and wanted is None
        ):
            decreases.append(
                _zero_target_from(
                    existing
                )
            )

            continue

        if (
            wanted is not None
            and existing is None
        ):
            increases.append(
                wanted
            )

    key = lambda target: (
        target.event_date,
        target.return_source_id,
        target.economic_source_id,
    )

    decreases.sort(
        key=key
    )

    increases.sort(
        key=key
    )

    return tuple(
        decreases
        + increases
    )


async def _load_trade_return_history(
    db: AsyncSession,
    *,
    company_id: int,
    fulfillment_id: int,
    fulfillment_line_id: int,
) -> tuple[
    TradeReturnEvent,
    ...,
]:
    return tuple(
        (
            await db.execute(
                select(
                    TradeReturnEvent
                )
                .where(
                    TradeReturnEvent.company_id
                    == company_id,
                    TradeReturnEvent.original_fulfillment_id
                    == fulfillment_id,
                    (
                        TradeReturnEvent
                        .original_fulfillment_line_id
                        == fulfillment_line_id
                    ),
                    TradeReturnEvent.direction
                    == "sale",
                )
                .order_by(
                    TradeReturnEvent.id
                )
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )


def _build_active_return_candidates(
    *,
    history: Iterable[
        TradeReturnEvent
    ],
) -> tuple[
    TradeReturnCandidate,
    ...,
]:
    active = _active_original_rows(
        history,
        label="TradeReturnEvent",
    )

    candidates = []

    for event in active:
        if (
            _enum_value(
                event.direction
            )
            != "sale"
        ):
            raise (
                SalesReturnRecognitionReconciliationDataIntegrityError(
                    "Sales Return reconciliation contains "
                    "non-SALE TradeReturnEvent"
                )
            )

        quantity = _decimal(
            event.returned_quantity
        )

        if quantity <= ZERO:
            raise (
                SalesReturnRecognitionReconciliationDataIntegrityError(
                    "Active TradeReturnEvent quantity "
                    "must be greater than zero"
                )
            )

        candidates.append(
            TradeReturnCandidate(
                source_id=event.id,
                event_date=event.return_date,
                quantity=quantity,
            )
        )

    return tuple(
        sorted(
            candidates,
            key=lambda candidate: (
                candidate.event_date,
                candidate.source_id,
            ),
        )
    )


async def _load_sales_capacity_sources(
    db: AsyncSession,
    *,
    company_id: int,
    fulfillment_id: int,
    fulfillment_line_id: int,
) -> tuple[
    SalesReturnRecognitionCapacitySource,
    ...,
]:
    """
    Rebuild active SalesRecognitionEvent versions for all invoice
    allocations of one original fulfillment line.

    FIFO identity deliberately uses InvoiceFulfillmentAllocation.id.
    Exact immutable provenance separately stores the active
    SalesRecognitionEvent.id.
    """

    rows = (
        await db.execute(
            select(
                SalesRecognitionEvent,
                InvoiceFulfillmentAllocation,
            )
            .join(
                InvoiceFulfillmentAllocation,
                and_(
                    (
                        InvoiceFulfillmentAllocation.company_id
                        == SalesRecognitionEvent.company_id
                    ),
                    (
                        InvoiceFulfillmentAllocation.id
                        == (
                            SalesRecognitionEvent
                            .invoice_fulfillment_allocation_id
                        )
                    ),
                ),
            )
            .where(
                SalesRecognitionEvent.company_id
                == company_id,
                (
                    InvoiceFulfillmentAllocation
                    .fulfillment_id
                    == fulfillment_id
                ),
                (
                    InvoiceFulfillmentAllocation
                    .fulfillment_line_id
                    == fulfillment_line_id
                ),
            )
            .order_by(
                SalesRecognitionEvent.id
            )
            .with_for_update()
        )
    ).all()

    sales_events = tuple(
        event
        for event, _allocation
        in rows
    )

    active_events = _active_original_rows(
        sales_events,
        label="SalesRecognitionEvent",
    )

    allocation_by_event_id = {
        event.id: allocation
        for event, allocation
        in rows
    }

    by_allocation = {}

    for event in active_events:
        allocation = (
            allocation_by_event_id.get(
                event.id
            )
        )

        if allocation is None:
            raise (
                SalesReturnRecognitionReconciliationDataIntegrityError(
                    "Active SalesRecognitionEvent "
                    "allocation row is missing"
                )
            )

        if (
            allocation.id
            in by_allocation
        ):
            raise (
                SalesReturnRecognitionReconciliationDataIntegrityError(
                    "One InvoiceFulfillmentAllocation "
                    "has more than one active "
                    "SalesRecognitionEvent"
                )
            )

        if (
            _enum_value(
                allocation.status
            )
            != "active"
        ):
            raise (
                SalesReturnRecognitionReconciliationDataIntegrityError(
                    "Active SalesRecognitionEvent references "
                    "non-ACTIVE InvoiceFulfillmentAllocation"
                )
            )

        quantity = _decimal(
            event.recognized_quantity
        )

        gross = _decimal(
            event.recognized_gross_amount
        )

        tax = _decimal(
            event.recognized_tax_amount
        )

        if quantity <= ZERO:
            raise (
                SalesReturnRecognitionReconciliationDataIntegrityError(
                    "Active SalesRecognitionEvent quantity "
                    "must be greater than zero"
                )
            )

        if gross <= ZERO:
            raise (
                SalesReturnRecognitionReconciliationDataIntegrityError(
                    "Active SalesRecognitionEvent gross "
                    "must be greater than zero"
                )
            )

        if (
            tax < ZERO
            or tax > gross
        ):
            raise (
                SalesReturnRecognitionReconciliationDataIntegrityError(
                    "Active SalesRecognitionEvent tax "
                    "amount is invalid"
                )
            )

        source = SalesReturnRecognitionCapacitySource(
            allocation_id=allocation.id,
            sales_recognition_event_id=event.id,
            event_date=event.recognition_date,
            quantity=quantity,
            gross_amount=gross,
            tax_amount=tax,
            currency_code=_currency(
                event.currency_code
            ),
        )

        by_allocation[
            allocation.id
        ] = source

    return tuple(
        sorted(
            by_allocation.values(),
            key=lambda source: (
                source.event_date,
                source.allocation_id,
            ),
        )
    )


async def _load_sales_return_recognition_history(
    db: AsyncSession,
    *,
    company_id: int,
    fulfillment_id: int,
    fulfillment_line_id: int,
) -> tuple[
    SalesReturnRecognitionEvent,
    ...,
]:
    return tuple(
        (
            await db.execute(
                select(
                    SalesReturnRecognitionEvent
                )
                .join(
                    TradeReturnEvent,
                    and_(
                        (
                            TradeReturnEvent.company_id
                            == (
                                SalesReturnRecognitionEvent
                                .company_id
                            )
                        ),
                        (
                            TradeReturnEvent.id
                            == (
                                SalesReturnRecognitionEvent
                                .trade_return_event_id
                            )
                        ),
                    ),
                )
                .where(
                    (
                        SalesReturnRecognitionEvent
                        .company_id
                        == company_id
                    ),
                    (
                        TradeReturnEvent
                        .original_fulfillment_id
                        == fulfillment_id
                    ),
                    (
                        TradeReturnEvent
                        .original_fulfillment_line_id
                        == fulfillment_line_id
                    ),
                )
                .order_by(
                    SalesReturnRecognitionEvent.id
                )
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )


def _active_history_currency_codes(
    history: Iterable[
        SalesReturnRecognitionEvent
    ],
) -> set[str]:
    active = _active_original_rows(
        history,
        label="SalesReturnRecognitionEvent",
    )

    return {
        _currency(
            event.currency_code
        )
        for event in active
    }


def _desired_targets_from_sources(
    *,
    candidates: tuple[
        TradeReturnCandidate,
        ...,
    ],
    capacity_sources: tuple[
        SalesReturnRecognitionCapacitySource,
        ...,
    ],
    currency_code: str,
) -> tuple[
    TradeReturnTarget,
    ...,
]:
    """
    Use durable allocation IDs for FIFO calculation, then translate
    the result to exact active SalesRecognitionEvent IDs.
    """

    calculation_capacities = tuple(
        TradeReturnEconomicCapacity(
            source_id=source.allocation_id,
            event_date=source.event_date,
            quantity=source.quantity,
            gross_amount=source.gross_amount,
            tax_amount=source.tax_amount,
            currency_code=source.currency_code,
        )
        for source in capacity_sources
    )

    calculated = build_trade_return_targets(
        capacities=calculation_capacities,
        candidates=candidates,
        currency_code=currency_code,
    )

    by_allocation = {
        source.allocation_id: source
        for source in capacity_sources
    }

    desired = []

    for target in calculated:
        source = by_allocation.get(
            target.economic_source_id
        )

        if source is None:
            raise (
                SalesReturnRecognitionReconciliationDataIntegrityError(
                    "Calculated Sales Return allocation "
                    "has no active SalesRecognitionEvent"
                )
            )

        desired.append(
            TradeReturnTarget(
                return_source_id=(
                    target.return_source_id
                ),
                economic_source_id=(
                    source.sales_recognition_event_id
                ),
                event_date=(
                    target.event_date
                ),
                quantity=(
                    target.quantity
                ),
                gross_amount=(
                    target.gross_amount
                ),
                tax_amount=(
                    target.tax_amount
                ),
                currency_code=(
                    target.currency_code
                ),
            )
        )

    return tuple(
        desired
    )


async def reconcile_sales_return_recognition_for_fulfillment_line(
    db: AsyncSession,
    *,
    company_id: int,
    fulfillment_id: int,
    fulfillment_line_id: int,
    created_by: int,
    adjustment_date: date | None = None,
) -> SalesReturnRecognitionReconciliationResult:
    """
    Reconcile complete economic Sales Return state for one original
    Sales fulfillment line.

    Physical source:
        active TradeReturnEvent

    Economic capacity:
        active SalesRecognitionEvent
        through the exact InvoiceFulfillmentAllocation

    Economic accounting later:
        Dr 704
        Cr 361

    VAT/RK remains separate.

    Caller owns COMMIT / ROLLBACK.
    """

    company_id = _positive_int(
        company_id,
        label="company_id",
    )

    fulfillment_id = _positive_int(
        fulfillment_id,
        label="fulfillment_id",
    )

    fulfillment_line_id = _positive_int(
        fulfillment_line_id,
        label="fulfillment_line_id",
    )

    created_by = _positive_int(
        created_by,
        label="created_by",
    )

    if (
        adjustment_date is not None
        and not isinstance(
            adjustment_date,
            date,
        )
    ):
        raise (
            SalesReturnRecognitionReconciliationDataIntegrityError(
                "adjustment_date must be a date"
            )
        )

    return_history = (
        await _load_trade_return_history(
            db,
            company_id=company_id,
            fulfillment_id=fulfillment_id,
            fulfillment_line_id=(
                fulfillment_line_id
            ),
        )
    )

    return_candidates = (
        _build_active_return_candidates(
            history=return_history
        )
    )

    capacity_sources = (
        await _load_sales_capacity_sources(
            db,
            company_id=company_id,
            fulfillment_id=fulfillment_id,
            fulfillment_line_id=(
                fulfillment_line_id
            ),
        )
    )

    history = (
        await _load_sales_return_recognition_history(
            db,
            company_id=company_id,
            fulfillment_id=fulfillment_id,
            fulfillment_line_id=(
                fulfillment_line_id
            ),
        )
    )

    currencies = {
        source.currency_code
        for source
        in capacity_sources
    }

    currencies.update(
        _active_history_currency_codes(
            history
        )
    )

    if len(
        currencies
    ) > 1:
        raise (
            SalesReturnRecognitionReconciliationDataIntegrityError(
                "Sales Return fulfillment line "
                "contains multiple currencies"
            )
        )

    if not currencies:
        if return_candidates:
            raise (
                SalesReturnRecognitionReconciliationDataIntegrityError(
                    "Active Sales Return exists without "
                    "active Sales economic capacity"
                )
            )

        return SalesReturnRecognitionReconciliationResult(
            fulfillment_id=fulfillment_id,
            fulfillment_line_id=fulfillment_line_id,
            currency_code=None,
            return_candidates=return_candidates,
            capacity_sources=capacity_sources,
            current_targets=(),
            desired_targets=(),
            reconciliation_targets=(),
            created_events=(),
        )

    currency_code = next(
        iter(
            currencies
        )
    )

    try:
        current_targets = (
            build_current_sales_return_recognition_targets(
                events=history,
                currency_code=currency_code,
            )
        )
    except (
        SalesReturnRecognitionDataIntegrityError
    ) as exc:
        raise (
            SalesReturnRecognitionReconciliationDataIntegrityError(
                "Could not rebuild current Sales Return "
                f"recognition state: {exc}"
            )
        ) from exc

    try:
        desired_targets = (
            _desired_targets_from_sources(
                candidates=return_candidates,
                capacity_sources=capacity_sources,
                currency_code=currency_code,
            )
        )
    except TradeReturnCalculationError as exc:
        raise (
            SalesReturnRecognitionReconciliationDataIntegrityError(
                "Sales Return allocation calculation "
                f"failed: {exc}"
            )
        ) from exc

    reconciliation_targets = (
        build_sales_return_recognition_reconciliation_targets(
            desired_targets=desired_targets,
            current_targets=current_targets,
        )
    )

    created_events = []

    for target in reconciliation_targets:
        created_events.extend(
            await reconcile_sales_return_recognition_source(
                db,
                company_id=company_id,
                target=target,
                currency_code=currency_code,
                created_by=created_by,
                reversal_date=adjustment_date,
            )
        )

    return SalesReturnRecognitionReconciliationResult(
        fulfillment_id=fulfillment_id,
        fulfillment_line_id=fulfillment_line_id,
        currency_code=currency_code,
        return_candidates=return_candidates,
        capacity_sources=capacity_sources,
        current_targets=current_targets,
        desired_targets=desired_targets,
        reconciliation_targets=(
            reconciliation_targets
        ),
        created_events=tuple(
            created_events
        ),
    )
