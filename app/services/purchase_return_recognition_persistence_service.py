from dataclasses import dataclass
from datetime import date
from decimal import (
    Decimal,
    InvalidOperation,
)

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.purchase_return_recognition_event import (
    PurchaseReturnRecognitionEvent,
)
from app.services.money_rounding import (
    round_currency_amount,
)
from app.services.purchase_return_recognition_calculation_service import (
    PurchaseReturnRecognitionTarget,
)


ZERO = Decimal("0")


class PurchaseReturnRecognitionPersistenceError(
    Exception
):
    """Base Purchase Return recognition persistence error."""


class PurchaseReturnRecognitionPersistenceDataIntegrityError(
    PurchaseReturnRecognitionPersistenceError
):
    """Persistent Purchase Return history is inconsistent."""


@dataclass(
    frozen=True,
    slots=True,
)
class PurchaseReturnRecognitionPersistenceResult:
    created_events: tuple[
        PurchaseReturnRecognitionEvent,
        ...,
    ]
    active_event: (
        PurchaseReturnRecognitionEvent
        | None
    )


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
        raise (
            PurchaseReturnRecognitionPersistenceDataIntegrityError(
                f"{field} must be greater than zero"
            )
        )

    return value


def _business_date(
    value: date,
    *,
    field: str,
) -> date:
    if not isinstance(
        value,
        date,
    ):
        raise (
            PurchaseReturnRecognitionPersistenceDataIntegrityError(
                f"{field} must be a date"
            )
        )

    return value


def _decimal(
    value,
    *,
    field: str,
) -> Decimal:
    try:
        result = (
            value
            if isinstance(
                value,
                Decimal,
            )
            else Decimal(
                str(
                    value
                )
            )
        )
    except (
        InvalidOperation,
        TypeError,
        ValueError,
    ) as exc:
        raise (
            PurchaseReturnRecognitionPersistenceDataIntegrityError(
                f"{field} must be a finite Decimal"
            )
        ) from exc

    if not result.is_finite():
        raise (
            PurchaseReturnRecognitionPersistenceDataIntegrityError(
                f"{field} must be finite"
            )
        )

    return result


def _currency(
    value: str,
) -> str:
    normalized = str(
        value
    ).strip().upper()

    if (
        len(
            normalized
        )
        != 3
        or not normalized.isalpha()
    ):
        raise (
            PurchaseReturnRecognitionPersistenceDataIntegrityError(
                "currency_code must contain exactly "
                "three alphabetic characters"
            )
        )

    return normalized


def _money(
    value,
    *,
    currency_code: str,
    field: str,
) -> Decimal:
    raw = _decimal(
        value,
        field=field,
    )

    try:
        return round_currency_amount(
            amount=raw,
            currency_code=currency_code,
        )
    except Exception as exc:
        raise (
            PurchaseReturnRecognitionPersistenceDataIntegrityError(
                f"{field} cannot be rounded"
            )
        ) from exc


def _normalize_target(
    target: PurchaseReturnRecognitionTarget,
    *,
    trade_return_event_id: int,
    invoice_fulfillment_allocation_id: int,
) -> PurchaseReturnRecognitionTarget:
    if not isinstance(
        target,
        PurchaseReturnRecognitionTarget,
    ):
        raise (
            PurchaseReturnRecognitionPersistenceDataIntegrityError(
                "target must be "
                "PurchaseReturnRecognitionTarget"
            )
        )

    return_source_id = _positive_id(
        target.return_source_id,
        field="target return_source_id",
    )

    economic_source_id = _positive_id(
        target.economic_source_id,
        field="target economic_source_id",
    )

    if return_source_id != trade_return_event_id:
        raise (
            PurchaseReturnRecognitionPersistenceDataIntegrityError(
                "target TradeReturnEvent provenance mismatch"
            )
        )

    if (
        economic_source_id
        != invoice_fulfillment_allocation_id
    ):
        raise (
            PurchaseReturnRecognitionPersistenceDataIntegrityError(
                "target InvoiceFulfillmentAllocation "
                "provenance mismatch"
            )
        )

    event_date = _business_date(
        target.event_date,
        field="target event_date",
    )

    quantity = _decimal(
        target.quantity,
        field="target quantity",
    )

    if quantity <= ZERO:
        raise (
            PurchaseReturnRecognitionPersistenceDataIntegrityError(
                "target quantity must be greater than zero"
            )
        )

    currency_code = _currency(
        target.currency_code
    )

    base_amount = _money(
        target.base_amount,
        currency_code=currency_code,
        field="target base_amount",
    )

    gross_amount = _money(
        target.gross_amount,
        currency_code=currency_code,
        field="target gross_amount",
    )

    tax_amount = _money(
        target.tax_amount,
        currency_code=currency_code,
        field="target tax_amount",
    )

    if base_amount < ZERO:
        raise (
            PurchaseReturnRecognitionPersistenceDataIntegrityError(
                "target base_amount cannot be negative"
            )
        )

    if gross_amount < ZERO:
        raise (
            PurchaseReturnRecognitionPersistenceDataIntegrityError(
                "target gross_amount cannot be negative"
            )
        )

    if tax_amount < ZERO:
        raise (
            PurchaseReturnRecognitionPersistenceDataIntegrityError(
                "target tax_amount cannot be negative"
            )
        )

    if tax_amount > gross_amount:
        raise (
            PurchaseReturnRecognitionPersistenceDataIntegrityError(
                "target tax_amount cannot exceed gross_amount"
            )
        )

    return PurchaseReturnRecognitionTarget(
        return_source_id=return_source_id,
        economic_source_id=economic_source_id,
        event_date=event_date,
        quantity=quantity,
        base_amount=base_amount,
        gross_amount=gross_amount,
        tax_amount=tax_amount,
        currency_code=currency_code,
    )


def _target_from_event(
    event: PurchaseReturnRecognitionEvent,
) -> PurchaseReturnRecognitionTarget:
    currency_code = _currency(
        event.currency_code
    )

    quantity = _decimal(
        event.returned_quantity,
        field="event returned_quantity",
    )

    if quantity <= ZERO:
        raise (
            PurchaseReturnRecognitionPersistenceDataIntegrityError(
                "active event returned_quantity "
                "must be greater than zero"
            )
        )

    base_amount = _money(
        event.returned_base_amount,
        currency_code=currency_code,
        field="event returned_base_amount",
    )

    gross_amount = _money(
        event.returned_gross_amount,
        currency_code=currency_code,
        field="event returned_gross_amount",
    )

    tax_amount = _money(
        event.returned_tax_amount,
        currency_code=currency_code,
        field="event returned_tax_amount",
    )

    if base_amount < ZERO:
        raise (
            PurchaseReturnRecognitionPersistenceDataIntegrityError(
                "active event base amount cannot be negative"
            )
        )

    if (
        gross_amount < ZERO
        or tax_amount < ZERO
        or tax_amount > gross_amount
    ):
        raise (
            PurchaseReturnRecognitionPersistenceDataIntegrityError(
                "active event gross/tax snapshots are invalid"
            )
        )

    return PurchaseReturnRecognitionTarget(
        return_source_id=_positive_id(
            event.trade_return_event_id,
            field="event trade_return_event_id",
        ),
        economic_source_id=_positive_id(
            event.invoice_fulfillment_allocation_id,
            field=(
                "event "
                "invoice_fulfillment_allocation_id"
            ),
        ),
        event_date=_business_date(
            event.recognition_date,
            field="event recognition_date",
        ),
        quantity=quantity,
        base_amount=base_amount,
        gross_amount=gross_amount,
        tax_amount=tax_amount,
        currency_code=currency_code,
    )


def _same_target(
    left: PurchaseReturnRecognitionTarget,
    right: PurchaseReturnRecognitionTarget,
) -> bool:
    return left == right


def _active_original_events(
    history: tuple[
        PurchaseReturnRecognitionEvent,
        ...,
    ],
) -> tuple[
    PurchaseReturnRecognitionEvent,
    ...,
]:
    by_id = {}
    originals = []
    reversed_original_ids = set()

    for event in history:
        event_id = _positive_id(
            event.id,
            field="history event id",
        )

        if event_id in by_id:
            raise (
                PurchaseReturnRecognitionPersistenceDataIntegrityError(
                    "duplicate Purchase Return recognition "
                    "history event id"
                )
            )

        by_id[event_id] = event

        reversal_of_id = event.reversal_of_id

        if reversal_of_id is None:
            originals.append(
                event
            )
            continue

        reversal_of_id = _positive_id(
            reversal_of_id,
            field="history reversal_of_id",
        )

        if reversal_of_id in reversed_original_ids:
            raise (
                PurchaseReturnRecognitionPersistenceDataIntegrityError(
                    "Purchase Return recognition original "
                    "has more than one reversal"
                )
            )

        reversed_original_ids.add(
            reversal_of_id
        )

    original_ids = {
        event.id
        for event in originals
    }

    unknown_reversal_sources = (
        reversed_original_ids
        - original_ids
    )

    if unknown_reversal_sources:
        raise (
            PurchaseReturnRecognitionPersistenceDataIntegrityError(
                "Purchase Return recognition reversal "
                "references a non-original history row"
            )
        )

    return tuple(
        event
        for event in originals
        if event.id
        not in reversed_original_ids
    )


async def _load_pair_history(
    db: AsyncSession,
    *,
    company_id: int,
    trade_return_event_id: int,
    invoice_fulfillment_allocation_id: int,
) -> tuple[
    PurchaseReturnRecognitionEvent,
    ...,
]:
    result = await db.execute(
        select(
            PurchaseReturnRecognitionEvent
        )
        .where(
            (
                PurchaseReturnRecognitionEvent
                .company_id
                == company_id
            ),
            (
                PurchaseReturnRecognitionEvent
                .trade_return_event_id
                == trade_return_event_id
            ),
            (
                PurchaseReturnRecognitionEvent
                .invoice_fulfillment_allocation_id
                == invoice_fulfillment_allocation_id
            ),
        )
        .order_by(
            PurchaseReturnRecognitionEvent.id
        )
        .with_for_update()
    )

    return tuple(
        result.scalars().all()
    )


async def reconcile_purchase_return_recognition_source(
    db: AsyncSession,
    *,
    company_id: int,
    trade_return_event_id: int,
    invoice_fulfillment_allocation_id: int,
    created_by: int,
    target: (
        PurchaseReturnRecognitionTarget
        | None
    ),
    reversal_date: date | None = None,
) -> PurchaseReturnRecognitionPersistenceResult:
    """
    Reconcile one immutable provenance pair:

        TradeReturnEvent
        +
        InvoiceFulfillmentAllocation

    Transition rules:
    - no active original + target
        -> create original;
    - active original + identical target
        -> no-op;
    - active original + no target
        -> create immutable reversal;
    - active original + changed target
        -> reversal + full replacement.

    reversal_date is required only when an active original must be
    reversed. Replacement recognition_date comes from target.event_date.

    Caller owns COMMIT / ROLLBACK.
    """

    company_id = _positive_id(
        company_id,
        field="company_id",
    )

    trade_return_event_id = _positive_id(
        trade_return_event_id,
        field="trade_return_event_id",
    )

    invoice_fulfillment_allocation_id = _positive_id(
        invoice_fulfillment_allocation_id,
        field="invoice_fulfillment_allocation_id",
    )

    created_by = _positive_id(
        created_by,
        field="created_by",
    )

    normalized_target = None

    if target is not None:
        normalized_target = _normalize_target(
            target,
            trade_return_event_id=(
                trade_return_event_id
            ),
            invoice_fulfillment_allocation_id=(
                invoice_fulfillment_allocation_id
            ),
        )

    if reversal_date is not None:
        reversal_date = _business_date(
            reversal_date,
            field="reversal_date",
        )

    history = await _load_pair_history(
        db,
        company_id=company_id,
        trade_return_event_id=(
            trade_return_event_id
        ),
        invoice_fulfillment_allocation_id=(
            invoice_fulfillment_allocation_id
        ),
    )

    for event in history:
        if event.company_id != company_id:
            raise (
                PurchaseReturnRecognitionPersistenceDataIntegrityError(
                    "history company provenance mismatch"
                )
            )

        if (
            event.trade_return_event_id
            != trade_return_event_id
        ):
            raise (
                PurchaseReturnRecognitionPersistenceDataIntegrityError(
                    "history TradeReturnEvent "
                    "provenance mismatch"
                )
            )

        if (
            event.invoice_fulfillment_allocation_id
            != invoice_fulfillment_allocation_id
        ):
            raise (
                PurchaseReturnRecognitionPersistenceDataIntegrityError(
                    "history InvoiceFulfillmentAllocation "
                    "provenance mismatch"
                )
            )

    active = _active_original_events(
        history
    )

    if len(active) > 1:
        raise (
            PurchaseReturnRecognitionPersistenceDataIntegrityError(
                "Purchase Return recognition pair has "
                "more than one active original event"
            )
        )

    current = (
        active[0]
        if active
        else None
    )

    if current is None:
        if normalized_target is None:
            return (
                PurchaseReturnRecognitionPersistenceResult(
                    created_events=(),
                    active_event=None,
                )
            )

        original = PurchaseReturnRecognitionEvent(
            company_id=company_id,
            trade_return_event_id=(
                trade_return_event_id
            ),
            invoice_fulfillment_allocation_id=(
                invoice_fulfillment_allocation_id
            ),
            recognition_date=(
                normalized_target.event_date
            ),
            returned_quantity=(
                normalized_target.quantity
            ),
            returned_base_amount=(
                normalized_target.base_amount
            ),
            returned_gross_amount=(
                normalized_target.gross_amount
            ),
            returned_tax_amount=(
                normalized_target.tax_amount
            ),
            currency_code=(
                normalized_target.currency_code
            ),
            created_by=created_by,
            reversal_of_id=None,
        )

        db.add(
            original
        )

        await db.flush()

        return (
            PurchaseReturnRecognitionPersistenceResult(
                created_events=(
                    original,
                ),
                active_event=original,
            )
        )

    current_target = _target_from_event(
        current
    )

    if (
        normalized_target is not None
        and _same_target(
            current_target,
            normalized_target,
        )
    ):
        return (
            PurchaseReturnRecognitionPersistenceResult(
                created_events=(),
                active_event=current,
            )
        )

    if reversal_date is None:
        raise (
            PurchaseReturnRecognitionPersistenceDataIntegrityError(
                "reversal_date is required when an active "
                "Purchase Return recognition event changes"
            )
        )

    original_id = _positive_id(
        current.id,
        field="active event id",
    )

    reversal = PurchaseReturnRecognitionEvent(
        company_id=company_id,
        trade_return_event_id=(
            current.trade_return_event_id
        ),
        invoice_fulfillment_allocation_id=(
            current
            .invoice_fulfillment_allocation_id
        ),
        recognition_date=reversal_date,
        returned_quantity=(
            current.returned_quantity
        ),
        returned_base_amount=(
            current.returned_base_amount
        ),
        returned_gross_amount=(
            current.returned_gross_amount
        ),
        returned_tax_amount=(
            current.returned_tax_amount
        ),
        currency_code=(
            current.currency_code
        ),
        created_by=created_by,
        reversal_of_id=original_id,
    )

    db.add(
        reversal
    )

    created_events = [
        reversal,
    ]

    replacement_event = None

    if normalized_target is not None:
        replacement_event = (
            PurchaseReturnRecognitionEvent(
                company_id=company_id,
                trade_return_event_id=(
                    trade_return_event_id
                ),
                invoice_fulfillment_allocation_id=(
                    invoice_fulfillment_allocation_id
                ),
                recognition_date=(
                    normalized_target.event_date
                ),
                returned_quantity=(
                    normalized_target.quantity
                ),
                returned_base_amount=(
                    normalized_target.base_amount
                ),
                returned_gross_amount=(
                    normalized_target.gross_amount
                ),
                returned_tax_amount=(
                    normalized_target.tax_amount
                ),
                currency_code=(
                    normalized_target.currency_code
                ),
                created_by=created_by,
                reversal_of_id=None,
            )
        )

        db.add(
            replacement_event
        )

        created_events.append(
            replacement_event
        )

    await db.flush()

    return (
        PurchaseReturnRecognitionPersistenceResult(
            created_events=tuple(
                created_events
            ),
            active_event=replacement_event,
        )
    )
