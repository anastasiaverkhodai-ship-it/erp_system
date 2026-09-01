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
from app.services.sales_recognition_calculation_service import (
    SalesRecognitionDataIntegrityError,
    SalesRecognitionTarget,
)


ZERO = Decimal("0")


class SalesRecognitionPersistenceError(Exception):
    """Base persistent Sales recognition error."""


class SalesRecognitionSourceNotFoundError(
    SalesRecognitionPersistenceError
):
    """Invoice fulfillment recognition source was not found."""


@dataclass(
    frozen=True,
    slots=True,
)
class SalesRecognitionSourcePlan:
    """
    Immutable persistence action for one Sales recognition source.

    replacement_target is always the complete desired source state,
    never a monetary delta.

    This is intentional because SalesRecognitionEvent also stores
    recognized_quantity. Appending an amount-only rounding delta
    would duplicate quantity semantics.

    Therefore any mismatch of an already-active source is handled as:

        reverse current active original
        +
        create one full replacement target
    """

    reversal_event_ids: tuple[int, ...]
    replacement_target: (
        SalesRecognitionTarget
        | None
    )

    @property
    def is_noop(
        self,
    ) -> bool:
        return (
            not self.reversal_event_ids
            and self.replacement_target
            is None
        )


def _decimal(
    value,
) -> Decimal:
    return Decimal(
        str(value)
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
            raise (
                SalesRecognitionDataIntegrityError(
                    "Persistent original Sales recognition "
                    "event has no ID"
                )
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
    event_id = getattr(
        event,
        "id",
        None,
    )

    if (
        not isinstance(
            event_id,
            int,
        )
        or event_id <= 0
    ):
        raise SalesRecognitionDataIntegrityError(
            "Active Sales recognition event ID "
            "must be greater than zero"
        )

    source_id = getattr(
        event,
        "invoice_fulfillment_allocation_id",
        None,
    )

    if (
        not isinstance(
            source_id,
            int,
        )
        or source_id <= 0
    ):
        raise SalesRecognitionDataIntegrityError(
            "Active Sales recognition event must "
            "have a valid fulfillment source"
        )

    recognition_date = getattr(
        event,
        "recognition_date",
        None,
    )

    if not isinstance(
        recognition_date,
        date,
    ):
        raise SalesRecognitionDataIntegrityError(
            "Active Sales recognition event must "
            "have a recognition date"
        )

    event_currency = getattr(
        event,
        "currency_code",
        None,
    )

    if (
        event_currency
        != currency_code
    ):
        raise SalesRecognitionDataIntegrityError(
            "Active Sales recognition event "
            "currency does not match Invoice"
        )

    quantity = _decimal(
        getattr(
            event,
            "recognized_quantity",
            ZERO,
        )
    )

    gross = _decimal(
        getattr(
            event,
            "recognized_gross_amount",
            ZERO,
        )
    )

    tax = _decimal(
        getattr(
            event,
            "recognized_tax_amount",
            ZERO,
        )
    )

    if quantity <= ZERO:
        raise SalesRecognitionDataIntegrityError(
            "Active Sales recognition quantity "
            "must be greater than zero"
        )

    if gross <= ZERO:
        raise SalesRecognitionDataIntegrityError(
            "Active Sales recognition gross amount "
            "must be greater than zero"
        )

    if tax < ZERO:
        raise SalesRecognitionDataIntegrityError(
            "Active Sales recognition tax amount "
            "cannot be negative"
        )

    if tax > gross:
        raise SalesRecognitionDataIntegrityError(
            "Active Sales recognition tax amount "
            "cannot exceed gross amount"
        )


def _validate_target(
    target: SalesRecognitionTarget,
) -> None:
    if not isinstance(
        target,
        SalesRecognitionTarget,
    ):
        raise SalesRecognitionDataIntegrityError(
            "Sales recognition persistence target "
            "must be SalesRecognitionTarget"
        )

    if target.source_id <= 0:
        raise SalesRecognitionDataIntegrityError(
            "Sales recognition target source_id "
            "must be greater than zero"
        )

    if not isinstance(
        target.event_date,
        date,
    ):
        raise SalesRecognitionDataIntegrityError(
            "Sales recognition target event_date "
            "must be a date"
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

    if quantity < ZERO:
        raise SalesRecognitionDataIntegrityError(
            "Sales recognition target quantity "
            "cannot be negative"
        )

    if gross < ZERO:
        raise SalesRecognitionDataIntegrityError(
            "Sales recognition target gross amount "
            "cannot be negative"
        )

    if tax < ZERO:
        raise SalesRecognitionDataIntegrityError(
            "Sales recognition target tax amount "
            "cannot be negative"
        )

    if tax > gross:
        raise SalesRecognitionDataIntegrityError(
            "Sales recognition target tax amount "
            "cannot exceed gross amount"
        )

    zero_state = (
        quantity == ZERO
        and gross == ZERO
        and tax == ZERO
    )

    positive_state = (
        quantity > ZERO
        and gross > ZERO
    )

    if not (
        zero_state
        or positive_state
    ):
        raise SalesRecognitionDataIntegrityError(
            "Sales recognition target must be either "
            "fully zero or have positive quantity "
            "and gross amount"
        )


def build_current_sales_recognition_targets(
    *,
    events: Iterable,
    currency_code: str,
) -> tuple[
    SalesRecognitionTarget,
    ...,
]:
    """
    Rebuild current net source targets from immutable event history.

    Normal Sales Recognition lifecycle maintains at most one ACTIVE
    original event for each InvoiceFulfillmentAllocation source.

    Historical originals may exist, but once reversed they do not
    contribute to the current state.

    More than one simultaneously-active original for one source is
    treated as data corruption rather than summed. This prevents
    duplicate recognized_quantity.
    """

    if (
        not isinstance(
            currency_code,
            str,
        )
        or len(currency_code) != 3
    ):
        raise SalesRecognitionDataIntegrityError(
            "Sales recognition currency_code "
            "must contain exactly 3 characters"
        )

    active = _active_original_events(
        events
    )

    by_source = {}

    for event in active:
        _validate_active_event(
            event,
            currency_code=currency_code,
        )

        source_id = (
            event
            .invoice_fulfillment_allocation_id
        )

        if source_id in by_source:
            raise SalesRecognitionDataIntegrityError(
                "Sales recognition source has more "
                "than one active original event"
            )

        by_source[
            source_id
        ] = event

    targets = []

    for source_id, event in by_source.items():
        targets.append(
            SalesRecognitionTarget(
                source_id=source_id,
                event_date=(
                    event.recognition_date
                ),
                quantity=_decimal(
                    event.recognized_quantity
                ),
                gross_amount=_decimal(
                    event.recognized_gross_amount
                ),
                tax_amount=_decimal(
                    event.recognized_tax_amount
                ),
            )
        )

    return tuple(
        sorted(
            targets,
            key=lambda target: (
                target.event_date,
                target.source_id,
            ),
        )
    )


def build_sales_recognition_source_plan(
    *,
    events: Iterable,
    target: SalesRecognitionTarget,
    currency_code: str,
) -> SalesRecognitionSourcePlan:
    """
    Plan immutable persistence for one fulfillment source.

    New source:
        create one full target event.

    Exact current state:
        no-op.

    Existing source with any monetary mismatch:
        reverse current active original,
        then create one full replacement target.

    Zero target:
        reverse current active original only.

    event_date and quantity are immutable economic source identity.
    A change to either is a data-integrity failure.
    """

    _validate_target(
        target
    )

    current_targets = (
        build_current_sales_recognition_targets(
            events=events,
            currency_code=currency_code,
        )
    )

    current_by_source = {
        current.source_id: current
        for current in current_targets
    }

    current = current_by_source.get(
        target.source_id
    )

    active = _active_original_events(
        events
    )

    active_source_events = tuple(
        event
        for event in active
        if getattr(
            event,
            "invoice_fulfillment_allocation_id",
            None,
        )
        == target.source_id
    )

    if current is None:
        if target.is_zero:
            return SalesRecognitionSourcePlan(
                reversal_event_ids=(),
                replacement_target=None,
            )

        return SalesRecognitionSourcePlan(
            reversal_event_ids=(),
            replacement_target=target,
        )

    if (
        current.event_date
        != target.event_date
    ):
        raise SalesRecognitionDataIntegrityError(
            "Sales recognition source event_date "
            "changed unexpectedly"
        )

    if (
        _decimal(
            current.quantity
        )
        != _decimal(
            target.quantity
        )
        and not target.is_zero
    ):
        raise SalesRecognitionDataIntegrityError(
            "Sales recognition source quantity "
            "changed unexpectedly"
        )

    if (
        not target.is_zero
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
        return SalesRecognitionSourcePlan(
            reversal_event_ids=(),
            replacement_target=None,
        )

    reversal_ids = tuple(
        event.id
        for event in active_source_events
    )

    if len(reversal_ids) != 1:
        raise SalesRecognitionDataIntegrityError(
            "Sales recognition source must have "
            "exactly one active original event"
        )

    if target.is_zero:
        return SalesRecognitionSourcePlan(
            reversal_event_ids=(
                reversal_ids
            ),
            replacement_target=None,
        )

    return SalesRecognitionSourcePlan(
        reversal_event_ids=(
            reversal_ids
        ),
        replacement_target=target,
    )



async def _lock_sales_recognition_source(
    db: AsyncSession,
    *,
    company_id: int,
    source_id: int,
) -> InvoiceFulfillmentAllocation:
    source = (
        await db.execute(
            select(
                InvoiceFulfillmentAllocation
            )
            .where(
                (
                    InvoiceFulfillmentAllocation
                    .company_id
                    == company_id
                ),
                (
                    InvoiceFulfillmentAllocation
                    .id
                    == source_id
                ),
            )
            .with_for_update()
        )
    ).scalar_one_or_none()

    if source is None:
        raise SalesRecognitionSourceNotFoundError(
            "InvoiceFulfillmentAllocation "
            "Sales recognition source not found"
        )

    return source


async def _load_sales_recognition_events(
    db: AsyncSession,
    *,
    company_id: int,
    source_id: int,
    lock_rows: bool,
) -> tuple[
    SalesRecognitionEvent,
    ...,
]:
    statement = (
        select(
            SalesRecognitionEvent
        )
        .where(
            (
                SalesRecognitionEvent
                .company_id
                == company_id
            ),
            (
                SalesRecognitionEvent
                .invoice_fulfillment_allocation_id
                == source_id
            ),
        )
        .order_by(
            SalesRecognitionEvent.id
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


async def reconcile_sales_recognition_source(
    db: AsyncSession,
    *,
    company_id: int,
    target: SalesRecognitionTarget,
    currency_code: str,
    created_by: int,
    reversal_date: date | None = None,
) -> tuple[
    SalesRecognitionEvent,
    ...,
]:
    """
    Reconcile one InvoiceFulfillmentAllocation source to an exact
    commercial Sales recognition target.

    Existing rows are never updated.

    Persistence semantics:

        new source
            -> one full original event

        exact target
            -> no-op

        existing source target mismatch
            -> immutable reversal of active original
            -> one full replacement original

        zero target
            -> immutable reversal only

    The source row and its event history are row-locked before the
    persistence plan is built.
    """

    if company_id <= 0:
        raise ValueError(
            "company_id must be greater than zero"
        )

    if created_by <= 0:
        raise ValueError(
            "created_by must be greater than zero"
        )

    if not isinstance(
        target,
        SalesRecognitionTarget,
    ):
        raise SalesRecognitionDataIntegrityError(
            "target must be SalesRecognitionTarget"
        )

    if target.source_id <= 0:
        raise SalesRecognitionDataIntegrityError(
            "Sales recognition target source_id "
            "must be greater than zero"
        )

    effective_reversal_date = (
        reversal_date
        if reversal_date is not None
        else target.event_date
    )

    await _lock_sales_recognition_source(
        db,
        company_id=company_id,
        source_id=target.source_id,
    )

    events = (
        await _load_sales_recognition_events(
            db,
            company_id=company_id,
            source_id=target.source_id,
            lock_rows=True,
        )
    )

    plan = (
        build_sales_recognition_source_plan(
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
            raise SalesRecognitionDataIntegrityError(
                "Sales recognition event selected "
                "for reversal does not exist"
            )

        reversal = SalesRecognitionEvent(
            company_id=company_id,
            invoice_fulfillment_allocation_id=(
                original
                .invoice_fulfillment_allocation_id
            ),
            recognition_date=(
                effective_reversal_date
            ),
            recognized_quantity=(
                original.recognized_quantity
            ),
            recognized_gross_amount=(
                original.recognized_gross_amount
            ),
            recognized_tax_amount=(
                original.recognized_tax_amount
            ),
            currency_code=(
                original.currency_code
            ),
            created_by=created_by,
            reversal_of_id=(
                original.id
            ),
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
        if replacement.is_zero:
            raise SalesRecognitionDataIntegrityError(
                "Zero Sales recognition target "
                "cannot be persisted as an "
                "original event"
            )

        original = SalesRecognitionEvent(
            company_id=company_id,
            invoice_fulfillment_allocation_id=(
                replacement.source_id
            ),
            recognition_date=(
                replacement.event_date
            ),
            recognized_quantity=(
                replacement.quantity
            ),
            recognized_gross_amount=(
                replacement.gross_amount
            ),
            recognized_tax_amount=(
                replacement.tax_amount
            ),
            currency_code=currency_code,
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


async def get_persistent_sales_recognition_target(
    db: AsyncSession,
    *,
    company_id: int,
    source_id: int,
    currency_code: str,
) -> SalesRecognitionTarget | None:
    """
    Return the current persistent net target for one fulfillment
    source without mutating recognition history.
    """

    if company_id <= 0:
        raise ValueError(
            "company_id must be greater than zero"
        )

    if source_id <= 0:
        raise ValueError(
            "source_id must be greater than zero"
        )

    events = (
        await _load_sales_recognition_events(
            db,
            company_id=company_id,
            source_id=source_id,
            lock_rows=False,
        )
    )

    targets = (
        build_current_sales_recognition_targets(
            events=events,
            currency_code=currency_code,
        )
    )

    if not targets:
        return None

    if len(targets) != 1:
        raise SalesRecognitionDataIntegrityError(
            "One Sales recognition source produced "
            "more than one current target"
        )

    return targets[0]
