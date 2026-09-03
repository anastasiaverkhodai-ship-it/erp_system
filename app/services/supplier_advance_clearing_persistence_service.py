from dataclasses import dataclass
from datetime import date
from decimal import (
    Decimal,
    InvalidOperation,
)
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.invoice_fulfillment_allocation import (
    InvoiceFulfillmentAllocation,
)
from app.models.payment_settlement_allocation import (
    PaymentSettlementAllocation,
)
from app.models.supplier_advance_clearing_event import (
    SupplierAdvanceClearingEvent,
)
from app.services.invoice_fulfillment_allocation_types import (
    InvoiceFulfillmentAllocationStatus,
)
from app.services.payment_types import (
    PaymentSettlementAllocationStatus,
)
from app.services.supplier_advance_clearing_calculation_service import (
    SupplierAdvanceClearingTarget,
)


ZERO = Decimal("0.00")


class SupplierAdvanceClearingPersistenceError(
    Exception
):
    """Base persistent supplier-advance clearing error."""


class SupplierAdvanceClearingSettlementSourceNotFoundError(
    SupplierAdvanceClearingPersistenceError
):
    """Required commercial settlement source was not found."""


class SupplierAdvanceClearingLiabilitySourceNotFoundError(
    SupplierAdvanceClearingPersistenceError
):
    """Required economic-liability source was not found."""


class SupplierAdvanceClearingSourceStateError(
    SupplierAdvanceClearingPersistenceError
):
    """Source state cannot support positive clearing."""


class SupplierAdvanceClearingDataIntegrityError(
    SupplierAdvanceClearingPersistenceError
):
    """Persistent supplier-clearing data is inconsistent."""


@dataclass(
    frozen=True,
    slots=True,
)
class SupplierAdvanceClearingSourcePlan:
    """
    Immutable persistence action for one exact source pair.

    Source identity:
        PaymentSettlementAllocation.id
        +
        InvoiceFulfillmentAllocation.id

    New positive target:
        create one immutable original.

    Exact current target:
        no-op.

    Changed positive target:
        reverse current active original
        +
        create one full replacement.

    Zero target:
        reverse current active original only.

    replacement_target is always complete desired state,
    never a monetary delta.
    """

    reversal_event_ids: tuple[int, ...]
    replacement_target: (
        SupplierAdvanceClearingTarget
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
    try:
        amount = Decimal(
            str(value)
        )
    except (
        InvalidOperation,
        ValueError,
        TypeError,
    ) as exc:
        raise (
            SupplierAdvanceClearingDataIntegrityError(
                "Supplier advance clearing amount "
                "is not a valid Decimal"
            )
        ) from exc

    if not amount.is_finite():
        raise (
            SupplierAdvanceClearingDataIntegrityError(
                "Supplier advance clearing amount "
                "must be finite"
            )
        )

    return amount


def _currency(
    currency_code: str,
) -> str:
    if not isinstance(
        currency_code,
        str,
    ):
        raise (
            SupplierAdvanceClearingDataIntegrityError(
                "currency_code must be a string"
            )
        )

    normalized = (
        currency_code
        .strip()
        .upper()
    )

    if (
        len(normalized) != 3
        or not normalized.isalpha()
    ):
        raise (
            SupplierAdvanceClearingDataIntegrityError(
                "currency_code must contain "
                "exactly three letters"
            )
        )

    return normalized


def _positive_source_id(
    value,
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
            SupplierAdvanceClearingDataIntegrityError(
                f"{label} must be a positive integer"
            )
        )

    return value


def _validate_target(
    target: SupplierAdvanceClearingTarget,
    *,
    currency_code: str,
) -> SupplierAdvanceClearingTarget:
    if not isinstance(
        target,
        SupplierAdvanceClearingTarget,
    ):
        raise (
            SupplierAdvanceClearingDataIntegrityError(
                "target must be "
                "SupplierAdvanceClearingTarget"
            )
        )

    settlement_source_id = (
        _positive_source_id(
            target.settlement_source_id,
            label="settlement_source_id",
        )
    )

    liability_source_id = (
        _positive_source_id(
            target.liability_source_id,
            label="liability_source_id",
        )
    )

    if not isinstance(
        target.event_date,
        date,
    ):
        raise (
            SupplierAdvanceClearingDataIntegrityError(
                "target event_date must be a date"
            )
        )

    currency = _currency(
        currency_code
    )

    target_currency = _currency(
        target.currency_code
    )

    if target_currency != currency:
        raise (
            SupplierAdvanceClearingDataIntegrityError(
                "target currency does not match "
                "reconciliation currency"
            )
        )

    amount = _decimal(
        target.amount
    )

    if amount < ZERO:
        raise (
            SupplierAdvanceClearingDataIntegrityError(
                "target amount cannot be negative"
            )
        )

    return SupplierAdvanceClearingTarget(
        settlement_source_id=(
            settlement_source_id
        ),
        liability_source_id=(
            liability_source_id
        ),
        event_date=target.event_date,
        amount=amount,
        currency_code=currency,
    )


def _active_original_events(
    events: Iterable,
) -> tuple:
    """
    Return immutable originals that have not themselves
    been reversed.

    Reversal rows never become active originals.
    """

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

        if (
            not isinstance(
                event_id,
                int,
            )
            or event_id <= 0
        ):
            raise (
                SupplierAdvanceClearingDataIntegrityError(
                    "Persistent original supplier "
                    "advance clearing event must "
                    "have a positive ID"
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
    _positive_source_id(
        getattr(
            event,
            "payment_settlement_allocation_id",
            None,
        ),
        label=(
            "active payment_settlement_allocation_id"
        ),
    )

    _positive_source_id(
        getattr(
            event,
            "invoice_fulfillment_allocation_id",
            None,
        ),
        label=(
            "active invoice_fulfillment_allocation_id"
        ),
    )

    clearing_date = getattr(
        event,
        "clearing_date",
        None,
    )

    if not isinstance(
        clearing_date,
        date,
    ):
        raise (
            SupplierAdvanceClearingDataIntegrityError(
                "Active supplier advance clearing "
                "event must have a clearing date"
            )
        )

    if (
        _currency(
            getattr(
                event,
                "currency_code",
                "",
            )
        )
        != currency_code
    ):
        raise (
            SupplierAdvanceClearingDataIntegrityError(
                "Active supplier advance clearing "
                "event currency does not match "
                "reconciliation currency"
            )
        )

    amount = _decimal(
        getattr(
            event,
            "cleared_amount",
            ZERO,
        )
    )

    if amount <= ZERO:
        raise (
            SupplierAdvanceClearingDataIntegrityError(
                "Active supplier advance clearing "
                "amount must be greater than zero"
            )
        )


def build_current_supplier_advance_clearing_targets(
    *,
    events: Iterable,
    currency_code: str,
) -> tuple[
    SupplierAdvanceClearingTarget,
    ...,
]:
    """
    Rebuild current state from immutable event history.

    At most one ACTIVE original may exist for one exact
    settlement/liability source pair.
    """

    currency = _currency(
        currency_code
    )

    active = _active_original_events(
        events
    )

    by_source_pair = {}

    for event in active:
        _validate_active_event(
            event,
            currency_code=currency,
        )

        key = (
            event
            .payment_settlement_allocation_id,
            event
            .invoice_fulfillment_allocation_id,
        )

        if key in by_source_pair:
            raise (
                SupplierAdvanceClearingDataIntegrityError(
                    "Supplier advance clearing source "
                    "pair has more than one active "
                    "original event"
                )
            )

        by_source_pair[
            key
        ] = event

    targets = []

    for (
        settlement_source_id,
        liability_source_id,
    ), event in by_source_pair.items():
        targets.append(
            SupplierAdvanceClearingTarget(
                settlement_source_id=(
                    settlement_source_id
                ),
                liability_source_id=(
                    liability_source_id
                ),
                event_date=(
                    event.clearing_date
                ),
                amount=_decimal(
                    event.cleared_amount
                ),
                currency_code=(
                    currency
                ),
            )
        )

    return tuple(
        sorted(
            targets,
            key=lambda target: (
                target.event_date,
                target.settlement_source_id,
                target.liability_source_id,
            ),
        )
    )


def _validate_source_history_provenance(
    *,
    events: Iterable,
    target: SupplierAdvanceClearingTarget,
    currency_code: str,
) -> None:
    """
    Source-pair provenance is immutable.

    For one exact pair, replacement may change amount,
    but may not silently change accounting date/currency.
    """

    for event in tuple(
        events
    ):
        if (
            getattr(
                event,
                "reversal_of_id",
                None,
            )
            is not None
        ):
            continue

        if (
            getattr(
                event,
                "payment_settlement_allocation_id",
                None,
            )
            != target.settlement_source_id
            or getattr(
                event,
                "invoice_fulfillment_allocation_id",
                None,
            )
            != target.liability_source_id
        ):
            continue

        if (
            event.clearing_date
            != target.event_date
        ):
            raise (
                SupplierAdvanceClearingDataIntegrityError(
                    "Supplier advance clearing "
                    "historical event_date changed "
                    "unexpectedly"
                )
            )

        if (
            _currency(
                event.currency_code
            )
            != currency_code
        ):
            raise (
                SupplierAdvanceClearingDataIntegrityError(
                    "Supplier advance clearing "
                    "historical currency changed "
                    "unexpectedly"
                )
            )


def build_supplier_advance_clearing_source_plan(
    *,
    events: Iterable,
    target: SupplierAdvanceClearingTarget,
    currency_code: str,
) -> SupplierAdvanceClearingSourcePlan:
    """
    Plan immutable persistence for one exact
    settlement/liability source pair.
    """

    normalized_target = (
        _validate_target(
            target,
            currency_code=currency_code,
        )
    )

    currency = (
        normalized_target.currency_code
    )

    event_tuple = tuple(
        events
    )

    _validate_source_history_provenance(
        events=event_tuple,
        target=normalized_target,
        currency_code=currency,
    )

    current_targets = (
        build_current_supplier_advance_clearing_targets(
            events=event_tuple,
            currency_code=currency,
        )
    )

    key = (
        normalized_target.settlement_source_id,
        normalized_target.liability_source_id,
    )

    current_by_pair = {
        (
            current.settlement_source_id,
            current.liability_source_id,
        ): current
        for current in current_targets
    }

    current = current_by_pair.get(
        key
    )

    amount = _decimal(
        normalized_target.amount
    )

    if current is None:
        if amount == ZERO:
            return SupplierAdvanceClearingSourcePlan(
                reversal_event_ids=(),
                replacement_target=None,
            )

        return SupplierAdvanceClearingSourcePlan(
            reversal_event_ids=(),
            replacement_target=(
                normalized_target
            ),
        )

    if (
        current.event_date
        != normalized_target.event_date
    ):
        raise (
            SupplierAdvanceClearingDataIntegrityError(
                "Supplier advance clearing source "
                "pair event_date changed unexpectedly"
            )
        )

    if (
        _decimal(
            current.amount
        )
        == amount
    ):
        return SupplierAdvanceClearingSourcePlan(
            reversal_event_ids=(),
            replacement_target=None,
        )

    active = _active_original_events(
        event_tuple
    )

    active_source_events = tuple(
        event
        for event in active
        if (
            getattr(
                event,
                "payment_settlement_allocation_id",
                None,
            )
            == normalized_target
            .settlement_source_id
            and getattr(
                event,
                "invoice_fulfillment_allocation_id",
                None,
            )
            == normalized_target
            .liability_source_id
        )
    )

    reversal_event_ids = tuple(
        event.id
        for event in active_source_events
    )

    if not reversal_event_ids:
        raise (
            SupplierAdvanceClearingDataIntegrityError(
                "Current supplier advance clearing "
                "target has no active persistent "
                "source event"
            )
        )

    if amount == ZERO:
        return SupplierAdvanceClearingSourcePlan(
            reversal_event_ids=(
                reversal_event_ids
            ),
            replacement_target=None,
        )

    return SupplierAdvanceClearingSourcePlan(
        reversal_event_ids=(
            reversal_event_ids
        ),
        replacement_target=(
            normalized_target
        ),
    )


async def _lock_supplier_advance_settlement_source(
    db: AsyncSession,
    *,
    company_id: int,
    source_id: int,
) -> PaymentSettlementAllocation:
    source = (
        await db.execute(
            select(
                PaymentSettlementAllocation
            )
            .where(
                PaymentSettlementAllocation.company_id
                == company_id,
                PaymentSettlementAllocation.id
                == source_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()

    if source is None:
        raise (
            SupplierAdvanceClearingSettlementSourceNotFoundError(
                "PaymentSettlementAllocation "
                "supplier-advance source not found"
            )
        )

    return source


async def _lock_supplier_economic_liability_source(
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
                InvoiceFulfillmentAllocation.company_id
                == company_id,
                InvoiceFulfillmentAllocation.id
                == source_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()

    if source is None:
        raise (
            SupplierAdvanceClearingLiabilitySourceNotFoundError(
                "InvoiceFulfillmentAllocation "
                "economic-liability source not found"
            )
        )

    return source


def _validate_locked_source_identity(
    *,
    settlement_source: PaymentSettlementAllocation,
    liability_source: InvoiceFulfillmentAllocation,
    company_id: int,
    target: SupplierAdvanceClearingTarget,
) -> None:
    if (
        settlement_source.company_id
        != company_id
        or liability_source.company_id
        != company_id
    ):
        raise (
            SupplierAdvanceClearingDataIntegrityError(
                "Locked supplier clearing source "
                "company mismatch"
            )
        )

    if (
        settlement_source.id
        != target.settlement_source_id
    ):
        raise (
            SupplierAdvanceClearingDataIntegrityError(
                "Locked commercial settlement source "
                "does not match target"
            )
        )

    if (
        liability_source.id
        != target.liability_source_id
    ):
        raise (
            SupplierAdvanceClearingDataIntegrityError(
                "Locked economic-liability source "
                "does not match target"
            )
        )


def _validate_positive_source_states(
    *,
    settlement_source: PaymentSettlementAllocation,
    liability_source: InvoiceFulfillmentAllocation,
    amount: Decimal,
) -> None:
    """
    Positive clearing requires both immutable source sides
    to remain ACTIVE.

    Zero desired state is allowed after either source has
    been reversed because it exists specifically to reverse
    previously persisted clearing history.
    """

    if amount == ZERO:
        return

    try:
        settlement_status = (
            PaymentSettlementAllocationStatus(
                settlement_source.status
            )
        )
    except (
        ValueError,
        TypeError,
    ) as exc:
        raise (
            SupplierAdvanceClearingSourceStateError(
                "Unsupported payment settlement "
                "allocation status"
            )
        ) from exc

    try:
        liability_status = (
            InvoiceFulfillmentAllocationStatus(
                liability_source.status
            )
        )
    except (
        ValueError,
        TypeError,
    ) as exc:
        raise (
            SupplierAdvanceClearingSourceStateError(
                "Unsupported invoice fulfillment "
                "allocation status"
            )
        ) from exc

    if (
        settlement_status
        != PaymentSettlementAllocationStatus.ACTIVE
    ):
        raise (
            SupplierAdvanceClearingSourceStateError(
                "Positive supplier advance clearing "
                "requires ACTIVE "
                "PaymentSettlementAllocation"
            )
        )

    if (
        liability_status
        != InvoiceFulfillmentAllocationStatus.ACTIVE
    ):
        raise (
            SupplierAdvanceClearingSourceStateError(
                "Positive supplier advance clearing "
                "requires ACTIVE "
                "InvoiceFulfillmentAllocation"
            )
        )


async def _load_supplier_advance_clearing_events(
    db: AsyncSession,
    *,
    company_id: int,
    settlement_source_id: int,
    liability_source_id: int,
    lock_rows: bool,
) -> tuple[
    SupplierAdvanceClearingEvent,
    ...,
]:
    """
    Load complete immutable history for one exact
    settlement/liability pair.
    """

    statement = (
        select(
            SupplierAdvanceClearingEvent
        )
        .where(
            SupplierAdvanceClearingEvent.company_id
            == company_id,
            (
                SupplierAdvanceClearingEvent
                .payment_settlement_allocation_id
                == settlement_source_id
            ),
            (
                SupplierAdvanceClearingEvent
                .invoice_fulfillment_allocation_id
                == liability_source_id
            ),
        )
        .order_by(
            SupplierAdvanceClearingEvent.id
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


async def reconcile_supplier_advance_clearing_source(
    db: AsyncSession,
    *,
    company_id: int,
    target: SupplierAdvanceClearingTarget,
    currency_code: str,
    created_by: int,
    reversal_date: date | None = None,
) -> tuple[
    SupplierAdvanceClearingEvent,
    ...,
]:
    """
    Reconcile one exact commercial-settlement /
    economic-liability source pair.

    Existing accounting history is never updated/deleted.

    Lock order:
        PaymentSettlementAllocation
        -> InvoiceFulfillmentAllocation
        -> SupplierAdvanceClearingEvent history

    Positive original represents:
        Dr SUPPLIER_PAYABLES
        Cr SUPPLIER_ADVANCES

        GENERAL 291:
        Dr 631
        Cr 371

    Caller owns COMMIT / ROLLBACK.
    """

    if (
        not isinstance(
            company_id,
            int,
        )
        or isinstance(
            company_id,
            bool,
        )
        or company_id <= 0
    ):
        raise ValueError(
            "company_id must be greater than zero"
        )

    if (
        not isinstance(
            created_by,
            int,
        )
        or isinstance(
            created_by,
            bool,
        )
        or created_by <= 0
    ):
        raise ValueError(
            "created_by must be greater than zero"
        )

    normalized_target = (
        _validate_target(
            target,
            currency_code=currency_code,
        )
    )

    amount = _decimal(
        normalized_target.amount
    )

    effective_reversal_date = (
        reversal_date
        if reversal_date is not None
        else normalized_target.event_date
    )

    if not isinstance(
        effective_reversal_date,
        date,
    ):
        raise (
            SupplierAdvanceClearingDataIntegrityError(
                "reversal_date must be a date"
            )
        )

    settlement_source = (
        await _lock_supplier_advance_settlement_source(
            db,
            company_id=company_id,
            source_id=(
                normalized_target
                .settlement_source_id
            ),
        )
    )

    liability_source = (
        await _lock_supplier_economic_liability_source(
            db,
            company_id=company_id,
            source_id=(
                normalized_target
                .liability_source_id
            ),
        )
    )

    _validate_locked_source_identity(
        settlement_source=settlement_source,
        liability_source=liability_source,
        company_id=company_id,
        target=normalized_target,
    )

    _validate_positive_source_states(
        settlement_source=settlement_source,
        liability_source=liability_source,
        amount=amount,
    )

    events = (
        await _load_supplier_advance_clearing_events(
            db,
            company_id=company_id,
            settlement_source_id=(
                normalized_target
                .settlement_source_id
            ),
            liability_source_id=(
                normalized_target
                .liability_source_id
            ),
            lock_rows=True,
        )
    )

    plan = (
        build_supplier_advance_clearing_source_plan(
            events=events,
            target=normalized_target,
            currency_code=(
                normalized_target.currency_code
            ),
        )
    )

    if plan.is_noop:
        return ()

    event_by_id = {
        event.id: event
        for event in events
    }

    created: list[
        SupplierAdvanceClearingEvent
    ] = []

    for event_id in (
        plan.reversal_event_ids
    ):
        original = event_by_id.get(
            event_id
        )

        if original is None:
            raise (
                SupplierAdvanceClearingDataIntegrityError(
                    "Supplier advance clearing event "
                    "selected for reversal does not exist"
                )
            )

        if (
            effective_reversal_date
            < original.clearing_date
        ):
            raise (
                SupplierAdvanceClearingDataIntegrityError(
                    "Supplier advance clearing "
                    "reversal_date cannot precede "
                    "original clearing_date"
                )
            )

        reversal = (
            SupplierAdvanceClearingEvent(
                company_id=company_id,
                payment_settlement_allocation_id=(
                    original
                    .payment_settlement_allocation_id
                ),
                invoice_fulfillment_allocation_id=(
                    original
                    .invoice_fulfillment_allocation_id
                ),
                clearing_date=(
                    effective_reversal_date
                ),
                cleared_amount=(
                    original.cleared_amount
                ),
                currency_code=(
                    original.currency_code
                ),
                created_by=created_by,
                reversal_of_id=original.id,
            )
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
        replacement_amount = (
            _decimal(
                replacement.amount
            )
        )

        if replacement_amount <= ZERO:
            raise (
                SupplierAdvanceClearingDataIntegrityError(
                    "Zero supplier advance clearing "
                    "target cannot be persisted as "
                    "an original event"
                )
            )

        original = (
            SupplierAdvanceClearingEvent(
                company_id=company_id,
                payment_settlement_allocation_id=(
                    replacement
                    .settlement_source_id
                ),
                invoice_fulfillment_allocation_id=(
                    replacement
                    .liability_source_id
                ),
                clearing_date=(
                    replacement.event_date
                ),
                cleared_amount=(
                    replacement_amount
                ),
                currency_code=(
                    replacement.currency_code
                ),
                created_by=created_by,
                reversal_of_id=None,
            )
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


async def get_persistent_supplier_advance_clearing_target(
    db: AsyncSession,
    *,
    company_id: int,
    settlement_source_id: int,
    liability_source_id: int,
    currency_code: str,
) -> SupplierAdvanceClearingTarget | None:
    """Return current active pair state without mutation."""

    if company_id <= 0:
        raise ValueError(
            "company_id must be greater than zero"
        )

    _positive_source_id(
        settlement_source_id,
        label="settlement_source_id",
    )

    _positive_source_id(
        liability_source_id,
        label="liability_source_id",
    )

    events = (
        await _load_supplier_advance_clearing_events(
            db,
            company_id=company_id,
            settlement_source_id=(
                settlement_source_id
            ),
            liability_source_id=(
                liability_source_id
            ),
            lock_rows=False,
        )
    )

    targets = (
        build_current_supplier_advance_clearing_targets(
            events=events,
            currency_code=currency_code,
        )
    )

    matching = tuple(
        target
        for target in targets
        if (
            target.settlement_source_id
            == settlement_source_id
            and target.liability_source_id
            == liability_source_id
        )
    )

    if len(matching) > 1:
        raise (
            SupplierAdvanceClearingDataIntegrityError(
                "More than one active supplier "
                "advance clearing target exists "
                "for source pair"
            )
        )

    if not matching:
        return None

    return matching[0]
