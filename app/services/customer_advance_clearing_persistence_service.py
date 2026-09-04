from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Iterable

from sqlalchemy import (
    and_,
    select,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.customer_advance_clearing_event import (
    CustomerAdvanceClearingEvent,
)
from app.models.payment import Payment
from app.models.payment_settlement_allocation import (
    PaymentSettlementAllocation,
)
from app.models.sales_recognition_event import (
    SalesRecognitionEvent,
)
from app.services.customer_advance_clearing_calculation_service import (
    CustomerAdvanceClearingTarget,
    money,
)
from app.services.payment_types import (
    PaymentDirection,
    PaymentSettlementAllocationStatus,
    PaymentStatus,
)


ZERO = Decimal("0.00")


class CustomerAdvanceClearingPersistenceError(
    Exception
):
    """Base Customer Advance Clearing persistence error."""


class CustomerAdvanceClearingSourceNotFoundError(
    CustomerAdvanceClearingPersistenceError
):
    """Required settlement or receivable source does not exist."""


class CustomerAdvanceClearingSourceStateError(
    CustomerAdvanceClearingPersistenceError
):
    """Source state cannot support the requested clearing."""


class CustomerAdvanceClearingDataIntegrityError(
    CustomerAdvanceClearingPersistenceError
):
    """Immutable Customer Advance Clearing history is inconsistent."""


@dataclass(
    frozen=True,
    slots=True,
)
class CustomerAdvanceClearingSourcePlan:
    """
    Immutable persistence action for one exact source pair.

    replacement_target is the complete desired clearing state,
    never a monetary delta.

    new positive
        -> original

    exact current
        -> no-op

    changed positive
        -> reversal + full replacement

    zero
        -> reversal only
    """

    reversal_event_ids: tuple[int, ...]
    replacement_target: (
        CustomerAdvanceClearingTarget
        | None
    )


def _positive_int(
    value,
    *,
    label: str,
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
        raise CustomerAdvanceClearingDataIntegrityError(
            f"{label} must be a positive integer"
        )

    return value


def _currency(
    value,
) -> str:
    if not isinstance(
        value,
        str,
    ):
        raise CustomerAdvanceClearingDataIntegrityError(
            "Currency must be a string"
        )

    normalized = (
        value
        .strip()
        .upper()
    )

    if (
        len(normalized) != 3
        or not normalized.isalpha()
    ):
        raise CustomerAdvanceClearingDataIntegrityError(
            "Currency must be a 3-letter code"
        )

    return normalized


def _amount(
    value,
    *,
    allow_zero: bool,
) -> Decimal:
    try:
        normalized = money(
            Decimal(
                str(value)
            )
        )
    except Exception as exc:
        raise CustomerAdvanceClearingDataIntegrityError(
            "Clearing amount is invalid"
        ) from exc

    if normalized < ZERO:
        raise CustomerAdvanceClearingDataIntegrityError(
            "Clearing amount cannot be negative"
        )

    if (
        not allow_zero
        and normalized == ZERO
    ):
        raise CustomerAdvanceClearingDataIntegrityError(
            "Persistent clearing event amount "
            "must be greater than zero"
        )

    return normalized


def _date(
    value,
    *,
    label: str,
) -> date:
    if not isinstance(
        value,
        date,
    ):
        raise CustomerAdvanceClearingDataIntegrityError(
            f"{label} must be a date"
        )

    return value


def _validate_target(
    target: CustomerAdvanceClearingTarget,
    *,
    currency_code: str,
) -> tuple[
    int,
    int,
    date,
    Decimal,
    str,
]:
    if not isinstance(
        target,
        CustomerAdvanceClearingTarget,
    ):
        raise CustomerAdvanceClearingDataIntegrityError(
            "target must be CustomerAdvanceClearingTarget"
        )

    settlement_id = _positive_int(
        target.settlement_source_id,
        label="settlement_source_id",
    )

    receivable_id = _positive_int(
        target.receivable_source_id,
        label="receivable_source_id",
    )

    event_date = _date(
        target.event_date,
        label="target event_date",
    )

    amount = _amount(
        target.amount,
        allow_zero=True,
    )

    expected_currency = _currency(
        currency_code
    )

    target_currency = _currency(
        target.currency_code
    )

    if (
        target_currency
        != expected_currency
    ):
        raise CustomerAdvanceClearingDataIntegrityError(
            "Target currency does not match "
            "reconciliation currency"
        )

    return (
        settlement_id,
        receivable_id,
        event_date,
        amount,
        target_currency,
    )


def _event_pair(
    event,
) -> tuple[
    int,
    int,
]:
    return (
        _positive_int(
            getattr(
                event,
                "payment_settlement_allocation_id",
                None,
            ),
            label=(
                "event payment_settlement_allocation_id"
            ),
        ),
        _positive_int(
            getattr(
                event,
                "sales_recognition_event_id",
                None,
            ),
            label=(
                "event sales_recognition_event_id"
            ),
        ),
    )


def _validate_history_event(
    event,
    *,
    currency_code: str,
) -> None:
    _positive_int(
        getattr(
            event,
            "id",
            None,
        ),
        label="event id",
    )

    _positive_int(
        getattr(
            event,
            "company_id",
            None,
        ),
        label="event company_id",
    )

    _event_pair(
        event
    )

    _date(
        getattr(
            event,
            "clearing_date",
            None,
        ),
        label="event clearing_date",
    )

    _amount(
        getattr(
            event,
            "cleared_amount",
            None,
        ),
        allow_zero=False,
    )

    event_currency = _currency(
        getattr(
            event,
            "currency_code",
            None,
        )
    )

    if (
        event_currency
        != currency_code
    ):
        raise CustomerAdvanceClearingDataIntegrityError(
            "Customer clearing event currency mismatch"
        )

    reversal_of_id = getattr(
        event,
        "reversal_of_id",
        None,
    )

    if reversal_of_id is not None:
        reversal_id = _positive_int(
            reversal_of_id,
            label="reversal_of_id",
        )

        if (
            reversal_id
            == getattr(
                event,
                "id",
                None,
            )
        ):
            raise CustomerAdvanceClearingDataIntegrityError(
                "Customer clearing event cannot "
                "reverse itself"
            )


def build_current_customer_advance_clearing_targets(
    *,
    events: Iterable,
    currency_code: str,
) -> tuple[
    CustomerAdvanceClearingTarget,
    ...,
]:
    """
    Rebuild current clearing state from immutable event history.

    Supports multiple settlement/receivable source pairs in one
    history collection.

    Historical originals and reversals remain immutable.
    """
    currency = _currency(
        currency_code
    )

    history = tuple(
        events
    )

    if not history:
        return ()

    by_id = {}
    pair_by_id = {}

    for event in history:
        _validate_history_event(
            event,
            currency_code=currency,
        )

        event_id = event.id

        if event_id in by_id:
            raise CustomerAdvanceClearingDataIntegrityError(
                "Duplicate customer clearing event id"
            )

        pair = _event_pair(
            event
        )

        by_id[event_id] = event
        pair_by_id[event_id] = pair

    reversed_original_ids = set()

    for event in history:
        reversal_of_id = getattr(
            event,
            "reversal_of_id",
            None,
        )

        if reversal_of_id is None:
            continue

        original = by_id.get(
            reversal_of_id
        )

        if original is None:
            raise CustomerAdvanceClearingDataIntegrityError(
                "Customer clearing reversal references "
                "missing original"
            )

        if (
            getattr(
                original,
                "reversal_of_id",
                None,
            )
            is not None
        ):
            raise CustomerAdvanceClearingDataIntegrityError(
                "Customer clearing reversal must "
                "reference an original"
            )

        if (
            pair_by_id[event.id]
            != pair_by_id[original.id]
        ):
            raise CustomerAdvanceClearingDataIntegrityError(
                "Customer clearing reversal changed "
                "source provenance"
            )

        original_amount = _amount(
            original.cleared_amount,
            allow_zero=False,
        )

        reversal_amount = _amount(
            event.cleared_amount,
            allow_zero=False,
        )

        if reversal_amount != original_amount:
            raise CustomerAdvanceClearingDataIntegrityError(
                "Customer clearing reversal amount "
                "differs from original"
            )

        if (
            _currency(event.currency_code)
            != _currency(original.currency_code)
        ):
            raise CustomerAdvanceClearingDataIntegrityError(
                "Customer clearing reversal currency "
                "differs from original"
            )

        if (
            event.clearing_date
            < original.clearing_date
        ):
            raise CustomerAdvanceClearingDataIntegrityError(
                "Customer clearing reversal date "
                "precedes original"
            )

        if original.id in reversed_original_ids:
            raise CustomerAdvanceClearingDataIntegrityError(
                "Customer clearing original has "
                "more than one reversal"
            )

        reversed_original_ids.add(
            original.id
        )

    active_originals_by_pair = {}

    for event in history:
        if (
            getattr(
                event,
                "reversal_of_id",
                None,
            )
            is not None
        ):
            continue

        if event.id in reversed_original_ids:
            continue

        pair = pair_by_id[
            event.id
        ]

        if pair in active_originals_by_pair:
            raise CustomerAdvanceClearingDataIntegrityError(
                "Customer clearing source pair has "
                "more than one ACTIVE original"
            )

        active_originals_by_pair[
            pair
        ] = event

    targets = []

    for (
        settlement_id,
        receivable_id,
    ), event in active_originals_by_pair.items():
        targets.append(
            CustomerAdvanceClearingTarget(
                settlement_source_id=settlement_id,
                receivable_source_id=receivable_id,
                event_date=event.clearing_date,
                amount=_amount(
                    event.cleared_amount,
                    allow_zero=False,
                ),
                currency_code=currency,
            )
        )

    return tuple(
        sorted(
            targets,
            key=lambda target: (
                target.event_date,
                target.settlement_source_id,
                target.receivable_source_id,
            ),
        )
    )


def _active_original_for_pair(
    *,
    events: Iterable,
    settlement_source_id: int,
    receivable_source_id: int,
    currency_code: str,
):
    history = tuple(
        events
    )

    current_targets = (
        build_current_customer_advance_clearing_targets(
            events=history,
            currency_code=currency_code,
        )
    )

    matching_targets = tuple(
        target
        for target in current_targets
        if (
            target.settlement_source_id
            == settlement_source_id
            and target.receivable_source_id
            == receivable_source_id
        )
    )

    if len(matching_targets) > 1:
        raise CustomerAdvanceClearingDataIntegrityError(
            "More than one current target for source pair"
        )

    if not matching_targets:
        return (
            None,
            None,
        )

    reversed_ids = {
        event.reversal_of_id
        for event in history
        if getattr(
            event,
            "reversal_of_id",
            None,
        )
        is not None
    }

    originals = tuple(
        event
        for event in history
        if (
            getattr(
                event,
                "reversal_of_id",
                None,
            )
            is None
            and event.id not in reversed_ids
            and _event_pair(event)
            == (
                settlement_source_id,
                receivable_source_id,
            )
        )
    )

    if len(originals) != 1:
        raise CustomerAdvanceClearingDataIntegrityError(
            "Could not identify one ACTIVE original "
            "for current source pair"
        )

    return (
        matching_targets[0],
        originals[0],
    )


def build_customer_advance_clearing_source_plan(
    *,
    events: Iterable,
    target: CustomerAdvanceClearingTarget,
    currency_code: str,
) -> CustomerAdvanceClearingSourcePlan:
    """
    Build one immutable persistence action for an exact pair.
    """
    (
        settlement_id,
        receivable_id,
        target_date,
        target_amount,
        currency,
    ) = _validate_target(
        target,
        currency_code=currency_code,
    )

    history = tuple(
        events
    )

    for event in history:
        if (
            _event_pair(event)
            != (
                settlement_id,
                receivable_id,
            )
        ):
            raise CustomerAdvanceClearingDataIntegrityError(
                "Source-pair persistence history "
                "contains another provenance pair"
            )

    current, current_event = (
        _active_original_for_pair(
            events=history,
            settlement_source_id=settlement_id,
            receivable_source_id=receivable_id,
            currency_code=currency,
        )
    )

    if target_amount == ZERO:
        if current is None:
            return CustomerAdvanceClearingSourcePlan(
                reversal_event_ids=(),
                replacement_target=None,
            )

        return CustomerAdvanceClearingSourcePlan(
            reversal_event_ids=(
                current_event.id,
            ),
            replacement_target=None,
        )

    if current is None:
        return CustomerAdvanceClearingSourcePlan(
            reversal_event_ids=(),
            replacement_target=target,
        )

    if (
        current.event_date
        != target_date
    ):
        raise CustomerAdvanceClearingDataIntegrityError(
            "Customer clearing source provenance date "
            "changed unexpectedly"
        )

    if (
        current.currency_code
        != currency
    ):
        raise CustomerAdvanceClearingDataIntegrityError(
            "Customer clearing source provenance "
            "currency changed unexpectedly"
        )

    if (
        current.amount
        == target_amount
    ):
        return CustomerAdvanceClearingSourcePlan(
            reversal_event_ids=(),
            replacement_target=None,
        )

    return CustomerAdvanceClearingSourcePlan(
        reversal_event_ids=(
            current_event.id,
        ),
        replacement_target=target,
    )


async def _lock_customer_settlement_source(
    db: AsyncSession,
    *,
    company_id: int,
    settlement_source_id: int,
    currency_code: str,
    require_active: bool,
) -> PaymentSettlementAllocation:
    row = (
        await db.execute(
            select(
                PaymentSettlementAllocation,
                Payment,
            )
            .join(
                Payment,
                and_(
                    Payment.company_id
                    == PaymentSettlementAllocation.company_id,
                    Payment.id
                    == PaymentSettlementAllocation.payment_id,
                ),
            )
            .where(
                PaymentSettlementAllocation.company_id
                == company_id,
                PaymentSettlementAllocation.id
                == settlement_source_id,
            )
            .with_for_update()
        )
    ).first()

    if row is None:
        raise CustomerAdvanceClearingSourceNotFoundError(
            "PaymentSettlementAllocation not found"
        )

    allocation, payment = row

    payment_currency = _currency(
        payment.currency_code
    )

    if (
        payment_currency
        != currency_code
    ):
        raise CustomerAdvanceClearingSourceStateError(
            "Settlement Payment currency does not "
            "match clearing currency"
        )

    if (
        payment.direction
        != PaymentDirection.INCOMING
    ):
        raise CustomerAdvanceClearingSourceStateError(
            "Customer advance clearing requires "
            "INCOMING Payment"
        )

    if require_active:
        if (
            allocation.status
            != PaymentSettlementAllocationStatus.ACTIVE
        ):
            raise CustomerAdvanceClearingSourceStateError(
                "Positive customer clearing requires "
                "ACTIVE settlement allocation"
            )

        if (
            payment.status
            != PaymentStatus.CONFIRMED
        ):
            raise CustomerAdvanceClearingSourceStateError(
                "Positive customer clearing requires "
                "CONFIRMED Payment"
            )

    return allocation


async def _lock_customer_economic_receivable_source(
    db: AsyncSession,
    *,
    company_id: int,
    receivable_source_id: int,
    currency_code: str,
    require_active: bool,
) -> SalesRecognitionEvent:
    event = (
        await db.execute(
            select(
                SalesRecognitionEvent
            )
            .where(
                SalesRecognitionEvent.company_id
                == company_id,
                SalesRecognitionEvent.id
                == receivable_source_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()

    if event is None:
        raise CustomerAdvanceClearingSourceNotFoundError(
            "SalesRecognitionEvent not found"
        )

    if (
        event.reversal_of_id
        is not None
    ):
        raise CustomerAdvanceClearingSourceStateError(
            "Customer clearing receivable source "
            "must be a SalesRecognitionEvent original"
        )

    if (
        _currency(
            event.currency_code
        )
        != currency_code
    ):
        raise CustomerAdvanceClearingSourceStateError(
            "SalesRecognitionEvent currency does not "
            "match clearing currency"
        )

    if require_active:
        reversal_id = (
            await db.execute(
                select(
                    SalesRecognitionEvent.id
                )
                .where(
                    SalesRecognitionEvent.company_id
                    == company_id,
                    SalesRecognitionEvent.reversal_of_id
                    == event.id,
                )
                .limit(1)
            )
        ).scalar_one_or_none()

        if reversal_id is not None:
            raise CustomerAdvanceClearingSourceStateError(
                "Positive customer clearing requires "
                "ACTIVE SalesRecognitionEvent original"
            )

    return event


async def _load_customer_advance_clearing_events(
    db: AsyncSession,
    *,
    company_id: int,
    settlement_source_id: int,
    receivable_source_id: int,
    lock_rows: bool,
) -> tuple[
    CustomerAdvanceClearingEvent,
    ...,
]:
    statement = (
        select(
            CustomerAdvanceClearingEvent
        )
        .where(
            CustomerAdvanceClearingEvent.company_id
            == company_id,
            (
                CustomerAdvanceClearingEvent
                .payment_settlement_allocation_id
                == settlement_source_id
            ),
            (
                CustomerAdvanceClearingEvent
                .sales_recognition_event_id
                == receivable_source_id
            ),
        )
        .order_by(
            CustomerAdvanceClearingEvent.id
        )
    )

    if lock_rows:
        statement = statement.with_for_update()

    return tuple(
        (
            await db.execute(
                statement
            )
        )
        .scalars()
        .all()
    )


async def reconcile_customer_advance_clearing_source(
    db: AsyncSession,
    *,
    company_id: int,
    target: CustomerAdvanceClearingTarget,
    currency_code: str,
    created_by: int,
    reversal_date: date | None = None,
) -> tuple[
    CustomerAdvanceClearingEvent,
    ...,
]:
    """
    Reconcile one exact settlement + Sales Recognition pair.

    Existing rows are never updated or deleted.

    Lock order:
        PaymentSettlementAllocation + Payment
        -> SalesRecognitionEvent
        -> CustomerAdvanceClearingEvent history

    Positive target requires both economic/commercial sources
    to remain ACTIVE.

    Zero target may reverse a previous clearing after one of
    its source records has already become inactive/reversed.

    Caller owns COMMIT / ROLLBACK.
    """
    company_id = _positive_int(
        company_id,
        label="company_id",
    )

    created_by = _positive_int(
        created_by,
        label="created_by",
    )

    (
        settlement_id,
        receivable_id,
        target_date,
        target_amount,
        currency,
    ) = _validate_target(
        target,
        currency_code=currency_code,
    )

    positive_target = (
        target_amount > ZERO
    )

    settlement = (
        await _lock_customer_settlement_source(
            db,
            company_id=company_id,
            settlement_source_id=settlement_id,
            currency_code=currency,
            require_active=positive_target,
        )
    )

    receivable = (
        await _lock_customer_economic_receivable_source(
            db,
            company_id=company_id,
            receivable_source_id=receivable_id,
            currency_code=currency,
            require_active=positive_target,
        )
    )

    if positive_target:
        settlement_capacity = _amount(
            settlement.amount,
            allow_zero=False,
        )

        receivable_capacity = _amount(
            receivable.recognized_gross_amount,
            allow_zero=False,
        )

        if target_amount > settlement_capacity:
            raise CustomerAdvanceClearingSourceStateError(
                "Clearing target exceeds settlement "
                "allocation capacity"
            )

        if target_amount > receivable_capacity:
            raise CustomerAdvanceClearingSourceStateError(
                "Clearing target exceeds economic "
                "receivable capacity"
            )

    history = (
        await _load_customer_advance_clearing_events(
            db,
            company_id=company_id,
            settlement_source_id=settlement_id,
            receivable_source_id=receivable_id,
            lock_rows=True,
        )
    )

    plan = (
        build_customer_advance_clearing_source_plan(
            events=history,
            target=target,
            currency_code=currency,
        )
    )

    if (
        not plan.reversal_event_ids
        and plan.replacement_target
        is None
    ):
        return ()

    by_id = {
        event.id: event
        for event in history
    }

    effective_reversal_date = (
        reversal_date
        if reversal_date is not None
        else target_date
    )

    effective_reversal_date = _date(
        effective_reversal_date,
        label="reversal_date",
    )

    created = []

    for original_id in (
        plan.reversal_event_ids
    ):
        original = by_id.get(
            original_id
        )

        if original is None:
            raise CustomerAdvanceClearingDataIntegrityError(
                "Persistence plan references missing "
                "CustomerAdvanceClearingEvent"
            )

        if (
            effective_reversal_date
            < original.clearing_date
        ):
            raise CustomerAdvanceClearingDataIntegrityError(
                "Customer clearing reversal date "
                "cannot precede original clearing date"
            )

        reversal = CustomerAdvanceClearingEvent(
            company_id=company_id,
            payment_settlement_allocation_id=(
                original
                .payment_settlement_allocation_id
            ),
            sales_recognition_event_id=(
                original
                .sales_recognition_event_id
            ),
            clearing_date=effective_reversal_date,
            cleared_amount=(
                original.cleared_amount
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

    if plan.reversal_event_ids:
        await db.flush()

    replacement = (
        plan.replacement_target
    )

    if replacement is not None:
        replacement_amount = _amount(
            replacement.amount,
            allow_zero=True,
        )

        if replacement_amount <= ZERO:
            raise CustomerAdvanceClearingDataIntegrityError(
                "Zero Customer Advance Clearing "
                "target cannot create original event"
            )

        original = CustomerAdvanceClearingEvent(
            company_id=company_id,
            payment_settlement_allocation_id=(
                replacement
                .settlement_source_id
            ),
            sales_recognition_event_id=(
                replacement
                .receivable_source_id
            ),
            clearing_date=(
                replacement.event_date
            ),
            cleared_amount=(
                replacement_amount
            ),
            currency_code=currency,
            created_by=created_by,
            reversal_of_id=None,
        )

        db.add(
            original
        )

        created.append(
            original
        )

    if created:
        await db.flush()

    return tuple(
        created
    )


async def get_persistent_customer_advance_clearing_target(
    db: AsyncSession,
    *,
    company_id: int,
    settlement_source_id: int,
    receivable_source_id: int,
    currency_code: str,
) -> CustomerAdvanceClearingTarget | None:
    company_id = _positive_int(
        company_id,
        label="company_id",
    )

    settlement_source_id = _positive_int(
        settlement_source_id,
        label="settlement_source_id",
    )

    receivable_source_id = _positive_int(
        receivable_source_id,
        label="receivable_source_id",
    )

    currency = _currency(
        currency_code
    )

    history = (
        await _load_customer_advance_clearing_events(
            db,
            company_id=company_id,
            settlement_source_id=settlement_source_id,
            receivable_source_id=receivable_source_id,
            lock_rows=False,
        )
    )

    targets = (
        build_current_customer_advance_clearing_targets(
            events=history,
            currency_code=currency,
        )
    )

    if not targets:
        return None

    if len(targets) != 1:
        raise CustomerAdvanceClearingDataIntegrityError(
            "Persistent source pair resolved to "
            "more than one current target"
        )

    return targets[0]
