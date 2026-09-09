from collections.abc import Iterable
from dataclasses import dataclass
from datetime import (
    date,
    datetime,
)
from decimal import (
    Decimal,
    InvalidOperation,
)

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.invoice_fulfillment_allocation import (
    InvoiceFulfillmentAllocation,
)
from app.models.purchase_value_correction_allocation_event import (
    PurchaseValueCorrectionAllocationEvent,
)
from app.models.trade_value_correction_event import (
    TradeValueCorrectionEvent,
)
from app.services.purchase_value_correction_allocation_calculation_service import (
    PurchaseValueCorrectionAllocationCandidate,
    PurchaseValueCorrectionAllocationDataIntegrityError,
    PurchaseValueCorrectionAllocationTarget,
    build_purchase_value_correction_allocation_targets,
)


ZERO = Decimal("0")


class PurchaseValueCorrectionAllocationPersistenceError(
    Exception
):
    """Base immutable Purchase Value Correction persistence error."""


class PurchaseValueCorrectionAllocationPersistenceDataIntegrityError(
    PurchaseValueCorrectionAllocationPersistenceError
):
    """Persisted correction-allocation history is inconsistent."""


class PurchaseValueCorrectionAllocationSourceNotFoundError(
    PurchaseValueCorrectionAllocationPersistenceError
):
    """Required immutable correction or fulfillment source was not found."""


@dataclass(
    frozen=True,
    slots=True,
)
class PurchaseValueCorrectionAllocationSourcePlan:
    """
    Immutable persistence plan for one source pair:

        TradeValueCorrectionEvent
        x
        InvoiceFulfillmentAllocation

    New non-noop target:
        create one original.

    Exact active target:
        no-op.

    Changed target:
        reverse active original
        then create one complete replacement.

    No-op target:
        reverse active original only.

    replacement_target is always complete desired state,
    never a monetary delta.
    """

    reversal_event_ids: tuple[int, ...]
    replacement_target: (
        PurchaseValueCorrectionAllocationTarget
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
        raise (
            PurchaseValueCorrectionAllocationPersistenceDataIntegrityError(
                f"{field} must be a positive integer"
            )
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
        raise (
            PurchaseValueCorrectionAllocationPersistenceDataIntegrityError(
                f"{field} must be a date"
            )
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
        raise (
            PurchaseValueCorrectionAllocationPersistenceDataIntegrityError(
                f"{field} must be numeric"
            )
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
        raise (
            PurchaseValueCorrectionAllocationPersistenceDataIntegrityError(
                f"{field} must be numeric"
            )
        ) from exc

    if not result.is_finite():
        raise (
            PurchaseValueCorrectionAllocationPersistenceDataIntegrityError(
                f"{field} must be finite"
            )
        )

    return result


def _normalized_target(
    target: PurchaseValueCorrectionAllocationTarget,
) -> PurchaseValueCorrectionAllocationTarget:
    if not isinstance(
        target,
        PurchaseValueCorrectionAllocationTarget,
    ):
        raise (
            PurchaseValueCorrectionAllocationPersistenceDataIntegrityError(
                "target must be "
                "PurchaseValueCorrectionAllocationTarget"
            )
        )

    correction_id = _positive_id(
        target.trade_value_correction_event_id,
        field=(
            "trade_value_correction_event_id"
        ),
    )

    source_id = _positive_id(
        target.invoice_fulfillment_allocation_id,
        field=(
            "invoice_fulfillment_allocation_id"
        ),
    )

    recognition_date = _business_date(
        target.recognition_date,
        field="recognition_date",
    )

    original_base = _decimal(
        target.original_allocated_base_amount,
        field=(
            "original_allocated_base_amount"
        ),
    )

    corrected_base = _decimal(
        target.corrected_allocated_base_amount,
        field=(
            "corrected_allocated_base_amount"
        ),
    )

    if (
        original_base < ZERO
        or corrected_base < ZERO
    ):
        raise (
            PurchaseValueCorrectionAllocationPersistenceDataIntegrityError(
                "allocated base amounts cannot be negative"
            )
        )

    currency = target.currency_code

    if (
        not isinstance(
            currency,
            str,
        )
        or len(
            currency
        ) != 3
    ):
        raise (
            PurchaseValueCorrectionAllocationPersistenceDataIntegrityError(
                "currency_code must contain exactly 3 characters"
            )
        )

    return (
        PurchaseValueCorrectionAllocationTarget(
            trade_value_correction_event_id=(
                correction_id
            ),
            invoice_fulfillment_allocation_id=(
                source_id
            ),
            recognition_date=(
                recognition_date
            ),
            original_allocated_base_amount=(
                original_base
            ),
            corrected_allocated_base_amount=(
                corrected_base
            ),
            currency_code=currency,
        )
    )


def _event_id(
    event: PurchaseValueCorrectionAllocationEvent,
) -> int:
    return _positive_id(
        event.id,
        field="event id",
    )


def _validate_event_source(
    *,
    event: PurchaseValueCorrectionAllocationEvent,
    target: PurchaseValueCorrectionAllocationTarget,
) -> None:
    if not isinstance(
        event,
        PurchaseValueCorrectionAllocationEvent,
    ):
        raise (
            PurchaseValueCorrectionAllocationPersistenceDataIntegrityError(
                "history row must be "
                "PurchaseValueCorrectionAllocationEvent"
            )
        )

    if (
        event.trade_value_correction_event_id
        != target.trade_value_correction_event_id
        or (
            event.invoice_fulfillment_allocation_id
            != target.invoice_fulfillment_allocation_id
        )
    ):
        raise (
            PurchaseValueCorrectionAllocationPersistenceDataIntegrityError(
                "history row source provenance "
                "does not match target"
            )
        )

    _business_date(
        event.recognition_date,
        field="event recognition_date",
    )

    original_base = _decimal(
        event.original_allocated_base_amount,
        field=(
            "event original_allocated_base_amount"
        ),
    )

    corrected_base = _decimal(
        event.corrected_allocated_base_amount,
        field=(
            "event corrected_allocated_base_amount"
        ),
    )

    if (
        original_base < ZERO
        or corrected_base < ZERO
        or original_base
        == corrected_base
    ):
        raise (
            PurchaseValueCorrectionAllocationPersistenceDataIntegrityError(
                "persistent correction-allocation "
                "amount state is invalid"
            )
        )

    if (
        event.currency_code
        != target.currency_code
    ):
        raise (
            PurchaseValueCorrectionAllocationPersistenceDataIntegrityError(
                "historical currency changed unexpectedly"
            )
        )


def _active_original_events(
    *,
    events: Iterable[
        PurchaseValueCorrectionAllocationEvent
    ],
    target: PurchaseValueCorrectionAllocationTarget,
) -> tuple[
    PurchaseValueCorrectionAllocationEvent,
    ...,
]:
    rows = tuple(
        events
    )

    event_by_id = {}

    for event in rows:
        _validate_event_source(
            event=event,
            target=target,
        )

        event_id = _event_id(
            event
        )

        if event_id in event_by_id:
            raise (
                PurchaseValueCorrectionAllocationPersistenceDataIntegrityError(
                    "Duplicate correction-allocation "
                    "event id in source history"
                )
            )

        event_by_id[
            event_id
        ] = event

    reversed_original_ids = set()

    for event in rows:
        if event.reversal_of_id is None:
            continue

        reversal_of_id = _positive_id(
            event.reversal_of_id,
            field="reversal_of_id",
        )

        original = event_by_id.get(
            reversal_of_id
        )

        if original is None:
            raise (
                PurchaseValueCorrectionAllocationPersistenceDataIntegrityError(
                    "Correction-allocation reversal "
                    "references event outside source history"
                )
            )

        if original.reversal_of_id is not None:
            raise (
                PurchaseValueCorrectionAllocationPersistenceDataIntegrityError(
                    "Correction-allocation reversal "
                    "cannot reverse another reversal"
                )
            )

        if (
            reversal_of_id
            in reversed_original_ids
        ):
            raise (
                PurchaseValueCorrectionAllocationPersistenceDataIntegrityError(
                    "Correction-allocation original has "
                    "multiple reversal rows"
                )
            )

        if (
            _decimal(
                event.original_allocated_base_amount,
                field=(
                    "reversal original base"
                ),
            )
            != _decimal(
                original.original_allocated_base_amount,
                field=(
                    "original original base"
                ),
            )
            or _decimal(
                event.corrected_allocated_base_amount,
                field=(
                    "reversal corrected base"
                ),
            )
            != _decimal(
                original.corrected_allocated_base_amount,
                field=(
                    "original corrected base"
                ),
            )
            or event.currency_code
            != original.currency_code
        ):
            raise (
                PurchaseValueCorrectionAllocationPersistenceDataIntegrityError(
                    "Reversal does not preserve "
                    "historical monetary state"
                )
            )

        reversed_original_ids.add(
            reversal_of_id
        )

    active = tuple(
        event
        for event in rows
        if (
            event.reversal_of_id
            is None
            and _event_id(
                event
            )
            not in reversed_original_ids
        )
    )

    if len(
        active
    ) > 1:
        raise (
            PurchaseValueCorrectionAllocationPersistenceDataIntegrityError(
                "Correction-allocation source has "
                "multiple active originals"
            )
        )

    return active


def _same_state(
    *,
    event: PurchaseValueCorrectionAllocationEvent,
    target: PurchaseValueCorrectionAllocationTarget,
) -> bool:
    return (
        event.recognition_date
        == target.recognition_date
        and _decimal(
            event.original_allocated_base_amount,
            field=(
                "event original base"
            ),
        )
        == target.original_allocated_base_amount
        and _decimal(
            event.corrected_allocated_base_amount,
            field=(
                "event corrected base"
            ),
        )
        == target.corrected_allocated_base_amount
        and event.currency_code
        == target.currency_code
    )


def build_purchase_value_correction_allocation_source_plan(
    *,
    events: Iterable[
        PurchaseValueCorrectionAllocationEvent
    ],
    target: PurchaseValueCorrectionAllocationTarget,
) -> PurchaseValueCorrectionAllocationSourcePlan:
    """
    Build immutable original/reversal/replacement plan
    for one correction x fulfillment source pair.
    """

    target = _normalized_target(
        target
    )

    active = _active_original_events(
        events=events,
        target=target,
    )

    if not active:
        if target.is_noop:
            return (
                PurchaseValueCorrectionAllocationSourcePlan(
                    reversal_event_ids=(),
                    replacement_target=None,
                )
            )

        return (
            PurchaseValueCorrectionAllocationSourcePlan(
                reversal_event_ids=(),
                replacement_target=target,
            )
        )

    current = active[
        0
    ]

    if (
        not target.is_noop
        and _same_state(
            event=current,
            target=target,
        )
    ):
        return (
            PurchaseValueCorrectionAllocationSourcePlan(
                reversal_event_ids=(),
                replacement_target=None,
            )
        )

    reversal_ids = (
        _event_id(
            current
        ),
    )

    if target.is_noop:
        return (
            PurchaseValueCorrectionAllocationSourcePlan(
                reversal_event_ids=(
                    reversal_ids
                ),
                replacement_target=None,
            )
        )

    return (
        PurchaseValueCorrectionAllocationSourcePlan(
            reversal_event_ids=(
                reversal_ids
            ),
            replacement_target=target,
        )
    )


async def _lock_trade_value_correction_event(
    db: AsyncSession,
    *,
    company_id: int,
    correction_event_id: int,
) -> TradeValueCorrectionEvent:
    event = (
        await db.execute(
            select(
                TradeValueCorrectionEvent
            )
            .where(
                TradeValueCorrectionEvent.company_id
                == company_id,
                TradeValueCorrectionEvent.id
                == correction_event_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()

    if event is None:
        raise (
            PurchaseValueCorrectionAllocationSourceNotFoundError(
                "TradeValueCorrectionEvent was not found"
            )
        )

    return event


async def _lock_invoice_fulfillment_allocation(
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
            PurchaseValueCorrectionAllocationSourceNotFoundError(
                "InvoiceFulfillmentAllocation was not found"
            )
        )

    return source


def _validate_source_provenance(
    *,
    correction: TradeValueCorrectionEvent,
    source: InvoiceFulfillmentAllocation,
    target: PurchaseValueCorrectionAllocationTarget,
) -> None:
    if correction.direction != "purchase":
        raise (
            PurchaseValueCorrectionAllocationPersistenceDataIntegrityError(
                "Purchase Value Correction requires "
                "a purchase TradeValueCorrectionEvent"
            )
        )

    if (
        correction.id
        != target.trade_value_correction_event_id
        or source.id
        != target.invoice_fulfillment_allocation_id
    ):
        raise (
            PurchaseValueCorrectionAllocationPersistenceDataIntegrityError(
                "Locked source identity does not match target"
            )
        )

    if (
        correction.company_id
        != source.company_id
    ):
        raise (
            PurchaseValueCorrectionAllocationPersistenceDataIntegrityError(
                "Correction and fulfillment company mismatch"
            )
        )

    if (
        correction.trade_document_id
        != source.invoice_id
        or correction.trade_document_line_id
        != source.invoice_line_id
        or correction.product_id
        != source.product_id
    ):
        raise (
            PurchaseValueCorrectionAllocationPersistenceDataIntegrityError(
                "Correction does not belong to "
                "the fulfillment invoice-line provenance"
            )
        )

    if (
        correction.currency_code
        != target.currency_code
    ):
        raise (
            PurchaseValueCorrectionAllocationPersistenceDataIntegrityError(
                "Correction currency does not match target"
            )
        )

    correction_date = _business_date(
        correction.correction_date,
        field="correction.correction_date",
    )

    if (
        target.recognition_date
        < correction_date
    ):
        raise (
            PurchaseValueCorrectionAllocationPersistenceDataIntegrityError(
                "recognition_date cannot precede "
                "correction_date"
            )
        )


async def _load_source_history(
    db: AsyncSession,
    *,
    company_id: int,
    target: PurchaseValueCorrectionAllocationTarget,
) -> tuple[
    PurchaseValueCorrectionAllocationEvent,
    ...,
]:
    rows = (
        await db.execute(
            select(
                PurchaseValueCorrectionAllocationEvent
            )
            .where(
                (
                    PurchaseValueCorrectionAllocationEvent
                    .company_id
                    == company_id
                ),
                (
                    PurchaseValueCorrectionAllocationEvent
                    .trade_value_correction_event_id
                    == target.trade_value_correction_event_id
                ),
                (
                    PurchaseValueCorrectionAllocationEvent
                    .invoice_fulfillment_allocation_id
                    == (
                        target
                        .invoice_fulfillment_allocation_id
                    )
                ),
            )
            .order_by(
                PurchaseValueCorrectionAllocationEvent.id
            )
            .with_for_update()
        )
    ).scalars().all()

    return tuple(
        rows
    )


async def reconcile_purchase_value_correction_allocation_source(
    db: AsyncSession,
    *,
    company_id: int,
    target: PurchaseValueCorrectionAllocationTarget,
    created_by: int,
    reversal_date: date | None = None,
) -> tuple[
    PurchaseValueCorrectionAllocationEvent,
    ...,
]:
    """
    Reconcile one immutable:

        TradeValueCorrectionEvent
        x
        InvoiceFulfillmentAllocation

    source to its complete desired state.

    Historical rows are never UPDATEd or DELETEd.

    Lock order:
        TradeValueCorrectionEvent
        -> InvoiceFulfillmentAllocation
        -> PurchaseValueCorrectionAllocationEvent history

    Caller owns COMMIT / ROLLBACK.
    """

    company_id = _positive_id(
        company_id,
        field="company_id",
    )

    created_by = _positive_id(
        created_by,
        field="created_by",
    )

    target = _normalized_target(
        target
    )

    effective_reversal_date = (
        reversal_date
        if reversal_date is not None
        else target.recognition_date
    )

    effective_reversal_date = _business_date(
        effective_reversal_date,
        field="reversal_date",
    )

    correction = (
        await _lock_trade_value_correction_event(
            db,
            company_id=company_id,
            correction_event_id=(
                target
                .trade_value_correction_event_id
            ),
        )
    )

    source = (
        await _lock_invoice_fulfillment_allocation(
            db,
            company_id=company_id,
            source_id=(
                target
                .invoice_fulfillment_allocation_id
            ),
        )
    )

    _validate_source_provenance(
        correction=correction,
        source=source,
        target=target,
    )

    history = (
        await _load_source_history(
            db,
            company_id=company_id,
            target=target,
        )
    )

    plan = (
        build_purchase_value_correction_allocation_source_plan(
            events=history,
            target=target,
        )
    )

    if plan.is_noop:
        return ()

    event_by_id = {
        _event_id(
            event
        ): event
        for event in history
    }

    created = []

    for event_id in (
        plan.reversal_event_ids
    ):
        original = event_by_id.get(
            event_id
        )

        if original is None:
            raise (
                PurchaseValueCorrectionAllocationPersistenceDataIntegrityError(
                    "Correction-allocation original selected "
                    "for reversal does not exist"
                )
            )

        if (
            effective_reversal_date
            < original.recognition_date
        ):
            raise (
                PurchaseValueCorrectionAllocationPersistenceDataIntegrityError(
                    "reversal_date cannot precede "
                    "original recognition_date"
                )
            )

        reversal = (
            PurchaseValueCorrectionAllocationEvent(
                company_id=company_id,
                trade_value_correction_event_id=(
                    original
                    .trade_value_correction_event_id
                ),
                invoice_fulfillment_allocation_id=(
                    original
                    .invoice_fulfillment_allocation_id
                ),
                recognition_date=(
                    effective_reversal_date
                ),
                original_allocated_base_amount=(
                    original
                    .original_allocated_base_amount
                ),
                corrected_allocated_base_amount=(
                    original
                    .corrected_allocated_base_amount
                ),
                currency_code=(
                    original.currency_code
                ),
                created_by=created_by,
                reversal_of_id=(
                    original.id
                ),
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
        if replacement.is_noop:
            raise (
                PurchaseValueCorrectionAllocationPersistenceDataIntegrityError(
                    "No-op correction-allocation target "
                    "cannot be persisted as an original"
                )
            )

        original = (
            PurchaseValueCorrectionAllocationEvent(
                company_id=company_id,
                trade_value_correction_event_id=(
                    replacement
                    .trade_value_correction_event_id
                ),
                invoice_fulfillment_allocation_id=(
                    replacement
                    .invoice_fulfillment_allocation_id
                ),
                recognition_date=(
                    replacement.recognition_date
                ),
                original_allocated_base_amount=(
                    replacement
                    .original_allocated_base_amount
                ),
                corrected_allocated_base_amount=(
                    replacement
                    .corrected_allocated_base_amount
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

    if created:
        await db.flush()

    return tuple(
        created
    )
