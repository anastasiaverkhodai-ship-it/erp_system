from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Iterable

from sqlalchemy import select
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
from app.services.trade_return_calculation_service import (
    TradeReturnTarget,
)


ZERO = Decimal("0")


class SalesReturnRecognitionPersistenceError(
    Exception
):
    """Base persistent Sales Return recognition error."""


class SalesReturnRecognitionDataIntegrityError(
    SalesReturnRecognitionPersistenceError
):
    """Persistent Sales Return recognition data is inconsistent."""


class SalesReturnRecognitionSourceNotFoundError(
    SalesReturnRecognitionPersistenceError
):
    """One immutable Sales Return recognition source was not found."""


class SalesReturnRecognitionInactiveSourceError(
    SalesReturnRecognitionPersistenceError
):
    """A positive target references an inactive economic source."""


@dataclass(
    frozen=True,
    slots=True,
)
class SalesReturnRecognitionSourcePlan:
    """
    Immutable persistence plan for one exact pair:

        TradeReturnEvent
        +
        SalesRecognitionEvent

    Semantics:

        new positive
            -> original

        exact
            -> no-op

        changed positive
            -> reverse active original
            -> create full replacement

        zero
            -> reverse active original only
    """

    reversal_event_ids: tuple[int, ...]
    replacement_target: TradeReturnTarget | None

    @property
    def is_noop(
        self,
    ) -> bool:
        return (
            not self.reversal_event_ids
            and self.replacement_target
            is None
        )


@dataclass(
    frozen=True,
    slots=True,
)
class SalesReturnRecognitionLockedSources:
    trade_return_event: TradeReturnEvent
    sales_recognition_event: SalesRecognitionEvent
    invoice_fulfillment_allocation: (
        InvoiceFulfillmentAllocation
    )
    trade_return_active: bool
    sales_recognition_active: bool


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
        raise SalesReturnRecognitionDataIntegrityError(
            f"{label} must be greater than zero"
        )

    return value


def _currency(
    value: str,
) -> str:
    if not isinstance(
        value,
        str,
    ):
        raise SalesReturnRecognitionDataIntegrityError(
            "currency_code must be a string"
        )

    normalized = value.strip().upper()

    if (
        len(
            normalized
        )
        != 3
        or not normalized.isalpha()
    ):
        raise SalesReturnRecognitionDataIntegrityError(
            "currency_code must contain exactly "
            "three alphabetic characters"
        )

    return normalized


def _enum_value(
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


def _target_is_zero(
    target: TradeReturnTarget,
) -> bool:
    return (
        _decimal(
            target.quantity
        )
        == ZERO
        and _decimal(
            target.gross_amount
        )
        == ZERO
        and _decimal(
            target.tax_amount
        )
        == ZERO
    )


def _validate_target(
    target: TradeReturnTarget,
    *,
    currency_code: str,
) -> None:
    if not isinstance(
        target,
        TradeReturnTarget,
    ):
        raise SalesReturnRecognitionDataIntegrityError(
            "target must be TradeReturnTarget"
        )

    _positive_int(
        target.return_source_id,
        label="return_source_id",
    )

    _positive_int(
        target.economic_source_id,
        label="economic_source_id",
    )

    if not isinstance(
        target.event_date,
        date,
    ):
        raise SalesReturnRecognitionDataIntegrityError(
            "target event_date must be a date"
        )

    expected_currency = _currency(
        currency_code
    )

    target_currency = _currency(
        target.currency_code
    )

    if target_currency != expected_currency:
        raise SalesReturnRecognitionDataIntegrityError(
            "target currency differs from reconciliation currency"
        )

    quantity = _decimal(
        target.quantity
    )

    gross = _decimal(
        target.gross_amount
    )

    tax = _decimal(
        target.tax_amount
    )

    if (
        quantity == ZERO
        and gross == ZERO
        and tax == ZERO
    ):
        return

    if quantity <= ZERO:
        raise SalesReturnRecognitionDataIntegrityError(
            "positive Sales Return target quantity "
            "must be greater than zero"
        )

    if gross <= ZERO:
        raise SalesReturnRecognitionDataIntegrityError(
            "positive Sales Return target gross amount "
            "must be greater than zero"
        )

    if tax < ZERO:
        raise SalesReturnRecognitionDataIntegrityError(
            "Sales Return target tax amount "
            "cannot be negative"
        )

    if tax > gross:
        raise SalesReturnRecognitionDataIntegrityError(
            "Sales Return target tax amount "
            "cannot exceed gross amount"
        )


def _active_original_events(
    events: Iterable,
) -> tuple:
    event_tuple = tuple(
        events
    )

    reversed_ids = {
        event.reversal_of_id
        for event in event_tuple
        if getattr(
            event,
            "reversal_of_id",
            None,
        )
        is not None
    }

    active = []

    for event in event_tuple:
        if (
            getattr(
                event,
                "reversal_of_id",
                None,
            )
            is not None
        ):
            continue

        event_id = getattr(
            event,
            "id",
            None,
        )

        if event_id is None:
            raise SalesReturnRecognitionDataIntegrityError(
                "Persistent original Sales Return "
                "recognition event has no ID"
            )

        if event_id in reversed_ids:
            continue

        active.append(
            event
        )

    return tuple(
        active
    )


def _validate_active_event(
    event,
    *,
    currency_code: str,
) -> None:
    _positive_int(
        event.id,
        label="event id",
    )

    _positive_int(
        event.trade_return_event_id,
        label="trade_return_event_id",
    )

    _positive_int(
        event.sales_recognition_event_id,
        label="sales_recognition_event_id",
    )

    if not isinstance(
        event.recognition_date,
        date,
    ):
        raise SalesReturnRecognitionDataIntegrityError(
            "Sales Return recognition_date must be a date"
        )

    if (
        _currency(
            event.currency_code
        )
        != _currency(
            currency_code
        )
    ):
        raise SalesReturnRecognitionDataIntegrityError(
            "Sales Return recognition event currency mismatch"
        )

    quantity = _decimal(
        event.returned_quantity
    )

    gross = _decimal(
        event.returned_gross_amount
    )

    tax = _decimal(
        event.returned_tax_amount
    )

    if quantity <= ZERO:
        raise SalesReturnRecognitionDataIntegrityError(
            "Active Sales Return quantity "
            "must be greater than zero"
        )

    if gross <= ZERO:
        raise SalesReturnRecognitionDataIntegrityError(
            "Active Sales Return gross amount "
            "must be greater than zero"
        )

    if tax < ZERO:
        raise SalesReturnRecognitionDataIntegrityError(
            "Active Sales Return tax amount "
            "cannot be negative"
        )

    if tax > gross:
        raise SalesReturnRecognitionDataIntegrityError(
            "Active Sales Return tax amount "
            "cannot exceed gross amount"
        )


def build_current_sales_return_recognition_targets(
    *,
    events: Iterable,
    currency_code: str,
) -> tuple[
    TradeReturnTarget,
    ...,
]:
    """
    Rebuild active pair state from immutable event history.

    Historical originals remain visible but are excluded after an
    immutable reversal references them.

    At most one simultaneously-active original is allowed for one
    Return/SalesRecognition pair.
    """

    normalized_currency = _currency(
        currency_code
    )

    active = _active_original_events(
        events
    )

    by_pair = {}

    for event in active:
        _validate_active_event(
            event,
            currency_code=normalized_currency,
        )

        pair = (
            event.trade_return_event_id,
            event.sales_recognition_event_id,
        )

        if pair in by_pair:
            raise SalesReturnRecognitionDataIntegrityError(
                "Sales Return recognition pair has more "
                "than one active original event"
            )

        by_pair[
            pair
        ] = event

    targets = []

    for (
        return_source_id,
        economic_source_id,
    ), event in by_pair.items():
        targets.append(
            TradeReturnTarget(
                return_source_id=(
                    return_source_id
                ),
                economic_source_id=(
                    economic_source_id
                ),
                event_date=(
                    event.recognition_date
                ),
                quantity=_decimal(
                    event.returned_quantity
                ),
                gross_amount=_decimal(
                    event.returned_gross_amount
                ),
                tax_amount=_decimal(
                    event.returned_tax_amount
                ),
                currency_code=(
                    normalized_currency
                ),
            )
        )

    return tuple(
        sorted(
            targets,
            key=lambda target: (
                target.event_date,
                target.return_source_id,
                target.economic_source_id,
            ),
        )
    )


def build_sales_return_recognition_source_plan(
    *,
    events: Iterable,
    target: TradeReturnTarget,
    currency_code: str,
) -> SalesReturnRecognitionSourcePlan:
    """
    Build immutable persistence for one exact pair.

    Pair identity:
        return_source_id
        economic_source_id

    recognition date and currency are immutable pair provenance.

    Quantity and monetary amounts are complete desired state and may
    change through reverse + full replacement because upstream FIFO
    or rounding redistribution may change the allocation.
    """

    _validate_target(
        target,
        currency_code=currency_code,
    )

    current_targets = (
        build_current_sales_return_recognition_targets(
            events=events,
            currency_code=currency_code,
        )
    )

    pair = (
        target.return_source_id,
        target.economic_source_id,
    )

    current = next(
        (
            item
            for item in current_targets
            if (
                item.return_source_id,
                item.economic_source_id,
            )
            == pair
        ),
        None,
    )

    active = _active_original_events(
        events
    )

    active_pair_events = tuple(
        event
        for event in active
        if (
            event.trade_return_event_id,
            event.sales_recognition_event_id,
        )
        == pair
    )

    if current is None:
        if _target_is_zero(
            target
        ):
            return SalesReturnRecognitionSourcePlan(
                reversal_event_ids=(),
                replacement_target=None,
            )

        return SalesReturnRecognitionSourcePlan(
            reversal_event_ids=(),
            replacement_target=target,
        )

    if (
        current.event_date
        != target.event_date
    ):
        raise SalesReturnRecognitionDataIntegrityError(
            "Sales Return pair recognition date "
            "changed unexpectedly"
        )

    if (
        _currency(
            current.currency_code
        )
        != _currency(
            target.currency_code
        )
    ):
        raise SalesReturnRecognitionDataIntegrityError(
            "Sales Return pair currency changed unexpectedly"
        )

    if (
        not _target_is_zero(
            target
        )
        and _decimal(
            current.quantity
        )
        == _decimal(
            target.quantity
        )
        and _decimal(
            current.gross_amount
        )
        == _decimal(
            target.gross_amount
        )
        and _decimal(
            current.tax_amount
        )
        == _decimal(
            target.tax_amount
        )
    ):
        return SalesReturnRecognitionSourcePlan(
            reversal_event_ids=(),
            replacement_target=None,
        )

    reversal_ids = tuple(
        event.id
        for event in active_pair_events
    )

    if len(
        reversal_ids
    ) != 1:
        raise SalesReturnRecognitionDataIntegrityError(
            "Sales Return pair must have exactly "
            "one active original event"
        )

    if _target_is_zero(
        target
    ):
        return SalesReturnRecognitionSourcePlan(
            reversal_event_ids=reversal_ids,
            replacement_target=None,
        )

    return SalesReturnRecognitionSourcePlan(
        reversal_event_ids=reversal_ids,
        replacement_target=target,
    )


async def _source_is_active_original(
    db: AsyncSession,
    *,
    model,
    company_id: int,
    source_id: int,
    reversal_of_id,
) -> bool:
    if reversal_of_id is not None:
        return False

    reversal_id = (
        await db.execute(
            select(
                model.id
            )
            .where(
                model.company_id
                == company_id,
                model.reversal_of_id
                == source_id,
            )
            .limit(
                1
            )
        )
    ).scalar_one_or_none()

    return reversal_id is None


async def _lock_sales_return_recognition_sources(
    db: AsyncSession,
    *,
    company_id: int,
    return_source_id: int,
    economic_source_id: int,
) -> SalesReturnRecognitionLockedSources:
    """
    Lock durable sources for one pair.

    We first read the immutable SalesRecognition source identity to
    resolve its InvoiceFulfillmentAllocation, then lock:

        InvoiceFulfillmentAllocation
        SalesRecognitionEvent
        TradeReturnEvent

    The allocation identity is immutable and is rechecked after lock.
    """

    snapshot = (
        await db.execute(
            select(
                SalesRecognitionEvent
            ).where(
                SalesRecognitionEvent.company_id
                == company_id,
                SalesRecognitionEvent.id
                == economic_source_id,
            )
        )
    ).scalar_one_or_none()

    if snapshot is None:
        raise SalesReturnRecognitionSourceNotFoundError(
            "SalesRecognitionEvent source not found"
        )

    allocation = (
        await db.execute(
            select(
                InvoiceFulfillmentAllocation
            )
            .where(
                InvoiceFulfillmentAllocation.company_id
                == company_id,
                InvoiceFulfillmentAllocation.id
                == (
                    snapshot
                    .invoice_fulfillment_allocation_id
                ),
            )
            .with_for_update()
        )
    ).scalar_one_or_none()

    if allocation is None:
        raise SalesReturnRecognitionSourceNotFoundError(
            "InvoiceFulfillmentAllocation source not found"
        )

    sales_event = (
        await db.execute(
            select(
                SalesRecognitionEvent
            )
            .where(
                SalesRecognitionEvent.company_id
                == company_id,
                SalesRecognitionEvent.id
                == economic_source_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()

    if sales_event is None:
        raise SalesReturnRecognitionSourceNotFoundError(
            "SalesRecognitionEvent disappeared during lock"
        )

    if (
        sales_event
        .invoice_fulfillment_allocation_id
        != allocation.id
    ):
        raise SalesReturnRecognitionDataIntegrityError(
            "SalesRecognitionEvent allocation identity "
            "changed during locking"
        )

    return_event = (
        await db.execute(
            select(
                TradeReturnEvent
            )
            .where(
                TradeReturnEvent.company_id
                == company_id,
                TradeReturnEvent.id
                == return_source_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()

    if return_event is None:
        raise SalesReturnRecognitionSourceNotFoundError(
            "TradeReturnEvent source not found"
        )

    trade_return_active = (
        await _source_is_active_original(
            db,
            model=TradeReturnEvent,
            company_id=company_id,
            source_id=return_event.id,
            reversal_of_id=(
                return_event.reversal_of_id
            ),
        )
    )

    sales_recognition_active = (
        await _source_is_active_original(
            db,
            model=SalesRecognitionEvent,
            company_id=company_id,
            source_id=sales_event.id,
            reversal_of_id=(
                sales_event.reversal_of_id
            ),
        )
    )

    return SalesReturnRecognitionLockedSources(
        trade_return_event=return_event,
        sales_recognition_event=sales_event,
        invoice_fulfillment_allocation=allocation,
        trade_return_active=trade_return_active,
        sales_recognition_active=(
            sales_recognition_active
        ),
    )


def _validate_locked_sources_for_target(
    *,
    sources: SalesReturnRecognitionLockedSources,
    target: TradeReturnTarget,
    currency_code: str,
) -> None:
    _validate_target(
        target,
        currency_code=currency_code,
    )

    return_event = (
        sources.trade_return_event
    )

    sales_event = (
        sources.sales_recognition_event
    )

    allocation = (
        sources.invoice_fulfillment_allocation
    )

    if (
        return_event.id
        != target.return_source_id
    ):
        raise SalesReturnRecognitionDataIntegrityError(
            "Locked TradeReturnEvent does not match target"
        )

    if (
        sales_event.id
        != target.economic_source_id
    ):
        raise SalesReturnRecognitionDataIntegrityError(
            "Locked SalesRecognitionEvent does not match target"
        )

    if (
        _enum_value(
            return_event.direction
        )
        != "sale"
    ):
        raise SalesReturnRecognitionDataIntegrityError(
            "Sales Return recognition requires "
            "TradeReturnEvent direction SALE"
        )

    if (
        allocation.fulfillment_id
        != return_event.original_fulfillment_id
        or allocation.fulfillment_line_id
        != return_event.original_fulfillment_line_id
        or allocation.product_id
        != return_event.product_id
    ):
        raise SalesReturnRecognitionDataIntegrityError(
            "SalesRecognitionEvent economic source "
            "does not belong to the original returned "
            "fulfillment line/product"
        )

    if (
        target.event_date
        != return_event.return_date
    ):
        raise SalesReturnRecognitionDataIntegrityError(
            "Sales Return target date differs "
            "from TradeReturnEvent return_date"
        )

    if (
        _currency(
            target.currency_code
        )
        != _currency(
            sales_event.currency_code
        )
    ):
        raise SalesReturnRecognitionDataIntegrityError(
            "Sales Return target currency differs "
            "from SalesRecognitionEvent"
        )

    if (
        sales_event.recognition_date
        > return_event.return_date
    ):
        raise SalesReturnRecognitionDataIntegrityError(
            "Sales Return precedes original "
            "Sales Recognition"
        )

    if _target_is_zero(
        target
    ):
        return

    if not sources.trade_return_active:
        raise SalesReturnRecognitionInactiveSourceError(
            "Positive Sales Return target references "
            "inactive TradeReturnEvent"
        )

    if not sources.sales_recognition_active:
        raise SalesReturnRecognitionInactiveSourceError(
            "Positive Sales Return target references "
            "inactive SalesRecognitionEvent"
        )

    if (
        _enum_value(
            allocation.status
        )
        != "active"
    ):
        raise SalesReturnRecognitionInactiveSourceError(
            "Positive Sales Return target references "
            "non-ACTIVE InvoiceFulfillmentAllocation"
        )

    if (
        _decimal(
            target.quantity
        )
        > _decimal(
            return_event.returned_quantity
        )
    ):
        raise SalesReturnRecognitionDataIntegrityError(
            "Pair returned quantity exceeds "
            "TradeReturnEvent quantity"
        )

    if (
        _decimal(
            target.quantity
        )
        > _decimal(
            sales_event.recognized_quantity
        )
    ):
        raise SalesReturnRecognitionDataIntegrityError(
            "Pair returned quantity exceeds "
            "SalesRecognitionEvent quantity"
        )

    if (
        _decimal(
            target.gross_amount
        )
        > _decimal(
            sales_event.recognized_gross_amount
        )
    ):
        raise SalesReturnRecognitionDataIntegrityError(
            "Pair returned gross amount exceeds "
            "SalesRecognitionEvent gross amount"
        )

    if (
        _decimal(
            target.tax_amount
        )
        > _decimal(
            sales_event.recognized_tax_amount
        )
    ):
        raise SalesReturnRecognitionDataIntegrityError(
            "Pair returned tax amount exceeds "
            "SalesRecognitionEvent tax amount"
        )


async def _load_pair_history(
    db: AsyncSession,
    *,
    company_id: int,
    return_source_id: int,
    economic_source_id: int,
    lock_rows: bool,
) -> tuple[
    SalesReturnRecognitionEvent,
    ...,
]:
    statement = (
        select(
            SalesReturnRecognitionEvent
        )
        .where(
            SalesReturnRecognitionEvent.company_id
            == company_id,
            (
                SalesReturnRecognitionEvent
                .trade_return_event_id
                == return_source_id
            ),
            (
                SalesReturnRecognitionEvent
                .sales_recognition_event_id
                == economic_source_id
            ),
        )
        .order_by(
            SalesReturnRecognitionEvent.id
        )
    )

    if lock_rows:
        statement = (
            statement.with_for_update()
        )

    return tuple(
        (
            await db.execute(
                statement
            )
        )
        .scalars()
        .all()
    )


async def reconcile_sales_return_recognition_source(
    db: AsyncSession,
    *,
    company_id: int,
    target: TradeReturnTarget,
    currency_code: str,
    created_by: int,
    reversal_date: date | None = None,
) -> tuple[
    SalesReturnRecognitionEvent,
    ...,
]:
    """
    Persist one exact Return/SalesRecognition pair.

    Caller owns COMMIT / ROLLBACK.
    """

    _positive_int(
        company_id,
        label="company_id",
    )

    _positive_int(
        created_by,
        label="created_by",
    )

    _validate_target(
        target,
        currency_code=currency_code,
    )

    if (
        reversal_date is not None
        and not isinstance(
            reversal_date,
            date,
        )
    ):
        raise SalesReturnRecognitionDataIntegrityError(
            "reversal_date must be a date"
        )

    effective_reversal_date = (
        reversal_date
        if reversal_date is not None
        else target.event_date
    )

    sources = (
        await _lock_sales_return_recognition_sources(
            db,
            company_id=company_id,
            return_source_id=(
                target.return_source_id
            ),
            economic_source_id=(
                target.economic_source_id
            ),
        )
    )

    _validate_locked_sources_for_target(
        sources=sources,
        target=target,
        currency_code=currency_code,
    )

    events = (
        await _load_pair_history(
            db,
            company_id=company_id,
            return_source_id=(
                target.return_source_id
            ),
            economic_source_id=(
                target.economic_source_id
            ),
            lock_rows=True,
        )
    )

    plan = (
        build_sales_return_recognition_source_plan(
            events=events,
            target=target,
            currency_code=currency_code,
        )
    )

    if plan.is_noop:
        return ()

    event_by_id = {
        event.id: event
        for event in events
    }

    created = []

    for event_id in (
        plan.reversal_event_ids
    ):
        original = event_by_id.get(
            event_id
        )

        if original is None:
            raise SalesReturnRecognitionDataIntegrityError(
                "Sales Return event selected for "
                "reversal does not exist"
            )

        reversal = SalesReturnRecognitionEvent(
            company_id=company_id,
            trade_return_event_id=(
                original.trade_return_event_id
            ),
            sales_recognition_event_id=(
                original.sales_recognition_event_id
            ),
            recognition_date=(
                effective_reversal_date
            ),
            returned_quantity=(
                original.returned_quantity
            ),
            returned_gross_amount=(
                original.returned_gross_amount
            ),
            returned_tax_amount=(
                original.returned_tax_amount
            ),
            currency_code=(
                original.currency_code
            ),
            created_by=created_by,
            reversal_of_id=original.id,
        )

        db.add(
            reversal
        )

        created.append(
            reversal
        )

    replacement = (
        plan.replacement_target
    )

    if replacement is not None:
        if _target_is_zero(
            replacement
        ):
            raise SalesReturnRecognitionDataIntegrityError(
                "Zero Sales Return target cannot "
                "be persisted as an original event"
            )

        original = SalesReturnRecognitionEvent(
            company_id=company_id,
            trade_return_event_id=(
                replacement.return_source_id
            ),
            sales_recognition_event_id=(
                replacement.economic_source_id
            ),
            recognition_date=(
                replacement.event_date
            ),
            returned_quantity=(
                replacement.quantity
            ),
            returned_gross_amount=(
                replacement.gross_amount
            ),
            returned_tax_amount=(
                replacement.tax_amount
            ),
            currency_code=_currency(
                replacement.currency_code
            ),
            created_by=created_by,
            reversal_of_id=None,
        )

        db.add(
            original
        )

        created.append(
            original
        )

    await db.flush()

    return tuple(
        created
    )
