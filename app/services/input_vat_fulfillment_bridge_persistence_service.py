from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.input_vat_fulfillment_bridge_event import (
    InputVatFulfillmentBridgeEvent,
)
from app.models.invoice_fulfillment_allocation import (
    InvoiceFulfillmentAllocation,
)
from app.models.tax_calculation import (
    TaxCalculation,
)
from app.services.input_vat_fulfillment_bridge_calculation_service import (
    InputVatFulfillmentBridgeDataIntegrityError,
    InputVatFulfillmentBridgeTarget,
)
from app.services.invoice_fulfillment_allocation_types import (
    InvoiceFulfillmentAllocationStatus,
)
from app.services.tax_types import (
    TaxDirection,
    TaxType,
)


ZERO = Decimal("0")


class InputVatFulfillmentBridgePersistenceError(
    Exception
):
    """Base persistent INPUT VAT fulfillment bridge error."""


class InputVatFulfillmentBridgeSourceNotFoundError(
    InputVatFulfillmentBridgePersistenceError
):
    """Required InvoiceFulfillmentAllocation was not found."""


class InputVatFulfillmentBridgeTaxCalculationNotFoundError(
    InputVatFulfillmentBridgePersistenceError
):
    """Required immutable INPUT VAT TaxCalculation was not found."""


class InputVatFulfillmentBridgeSourceStateError(
    InputVatFulfillmentBridgePersistenceError
):
    """Fulfillment allocation state cannot support the target."""


@dataclass(
    frozen=True,
    slots=True,
)
class InputVatFulfillmentBridgeSourcePlan:
    """
    Immutable persistence action for one fulfillment source.

    replacement_target is always the complete desired state,
    never a monetary delta.

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
    """

    reversal_event_ids: tuple[int, ...]
    replacement_target: (
        InputVatFulfillmentBridgeTarget
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


def _validate_currency_code(
    currency_code: str,
) -> None:
    if (
        not isinstance(
            currency_code,
            str,
        )
        or len(currency_code) != 3
    ):
        raise (
            InputVatFulfillmentBridgeDataIntegrityError(
                "INPUT VAT fulfillment bridge "
                "currency_code must contain "
                "exactly 3 characters"
            )
        )


def _active_original_events(
    events: Iterable,
) -> tuple:
    """
    Return original rows that have not themselves been reversed.

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
                InputVatFulfillmentBridgeDataIntegrityError(
                    "Persistent original INPUT VAT "
                    "fulfillment bridge event must "
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
    tax_calculation_id = getattr(
        event,
        "tax_calculation_id",
        None,
    )

    if (
        not isinstance(
            tax_calculation_id,
            int,
        )
        or tax_calculation_id <= 0
    ):
        raise (
            InputVatFulfillmentBridgeDataIntegrityError(
                "Active INPUT VAT bridge event must "
                "have a valid TaxCalculation"
            )
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
        raise (
            InputVatFulfillmentBridgeDataIntegrityError(
                "Active INPUT VAT bridge event must "
                "have a valid fulfillment source"
            )
        )

    bridge_date = getattr(
        event,
        "bridge_date",
        None,
    )

    if not isinstance(
        bridge_date,
        date,
    ):
        raise (
            InputVatFulfillmentBridgeDataIntegrityError(
                "Active INPUT VAT bridge event must "
                "have a bridge date"
            )
        )

    if (
        getattr(
            event,
            "currency_code",
            None,
        )
        != currency_code
    ):
        raise (
            InputVatFulfillmentBridgeDataIntegrityError(
                "Active INPUT VAT bridge event "
                "currency does not match target"
            )
        )

    amount = _decimal(
        getattr(
            event,
            "bridged_tax_amount",
            ZERO,
        )
    )

    if amount <= ZERO:
        raise (
            InputVatFulfillmentBridgeDataIntegrityError(
                "Active INPUT VAT bridge amount "
                "must be greater than zero"
            )
        )


def _validate_target(
    target: InputVatFulfillmentBridgeTarget,
    *,
    currency_code: str,
) -> None:
    if not isinstance(
        target,
        InputVatFulfillmentBridgeTarget,
    ):
        raise (
            InputVatFulfillmentBridgeDataIntegrityError(
                "INPUT VAT fulfillment bridge "
                "persistence target must be "
                "InputVatFulfillmentBridgeTarget"
            )
        )

    _validate_currency_code(
        currency_code
    )

    if (
        target.currency_code
        != currency_code
    ):
        raise (
            InputVatFulfillmentBridgeDataIntegrityError(
                "INPUT VAT fulfillment bridge target "
                "currency does not match "
                "reconciliation currency"
            )
        )

    if target.tax_calculation_id <= 0:
        raise (
            InputVatFulfillmentBridgeDataIntegrityError(
                "INPUT VAT fulfillment bridge "
                "tax_calculation_id must be "
                "greater than zero"
            )
        )

    if target.source_id <= 0:
        raise (
            InputVatFulfillmentBridgeDataIntegrityError(
                "INPUT VAT fulfillment bridge "
                "source_id must be greater than zero"
            )
        )

    if not isinstance(
        target.event_date,
        date,
    ):
        raise (
            InputVatFulfillmentBridgeDataIntegrityError(
                "INPUT VAT fulfillment bridge "
                "event_date must be a date"
            )
        )

    if _decimal(
        target.amount
    ) < ZERO:
        raise (
            InputVatFulfillmentBridgeDataIntegrityError(
                "INPUT VAT fulfillment bridge "
                "target amount cannot be negative"
            )
        )


def _validate_source_history_provenance(
    *,
    events: Iterable,
    target: InputVatFulfillmentBridgeTarget,
    currency_code: str,
) -> None:
    """
    Historical source provenance is immutable even if all previous
    originals have already been reversed.

    A replacement may change amount only.

    It may not silently change:
        TaxCalculation
        receipt event date
        currency
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
                "invoice_fulfillment_allocation_id",
                None,
            )
            != target.source_id
        ):
            continue

        if (
            event.tax_calculation_id
            != target.tax_calculation_id
        ):
            raise (
                InputVatFulfillmentBridgeDataIntegrityError(
                    "INPUT VAT fulfillment bridge "
                    "historical tax_calculation_id "
                    "changed unexpectedly"
                )
            )

        if (
            event.bridge_date
            != target.event_date
        ):
            raise (
                InputVatFulfillmentBridgeDataIntegrityError(
                    "INPUT VAT fulfillment bridge "
                    "historical event_date changed "
                    "unexpectedly"
                )
            )

        if (
            event.currency_code
            != currency_code
        ):
            raise (
                InputVatFulfillmentBridgeDataIntegrityError(
                    "INPUT VAT fulfillment bridge "
                    "historical currency changed "
                    "unexpectedly"
                )
            )


def build_current_input_vat_fulfillment_bridge_targets(
    *,
    events: Iterable,
    currency_code: str,
) -> tuple[
    InputVatFulfillmentBridgeTarget,
    ...,
]:
    """
    Rebuild current active bridge state from immutable history.

    There may be at most one ACTIVE original for one source.
    """

    _validate_currency_code(
        currency_code
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
            raise (
                InputVatFulfillmentBridgeDataIntegrityError(
                    "INPUT VAT fulfillment source "
                    "has more than one active "
                    "original bridge event"
                )
            )

        by_source[
            source_id
        ] = event

    targets = []

    for source_id, event in by_source.items():
        targets.append(
            InputVatFulfillmentBridgeTarget(
                tax_calculation_id=(
                    event.tax_calculation_id
                ),
                source_id=source_id,
                event_date=(
                    event.bridge_date
                ),
                amount=_decimal(
                    event.bridged_tax_amount
                ),
                currency_code=(
                    event.currency_code
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


def build_input_vat_fulfillment_bridge_source_plan(
    *,
    events: Iterable,
    target: InputVatFulfillmentBridgeTarget,
    currency_code: str,
) -> InputVatFulfillmentBridgeSourcePlan:
    """
    Plan immutable persistence for one fulfillment source.
    """

    _validate_target(
        target,
        currency_code=currency_code,
    )

    event_tuple = tuple(
        events
    )

    _validate_source_history_provenance(
        events=event_tuple,
        target=target,
        currency_code=currency_code,
    )

    current_targets = (
        build_current_input_vat_fulfillment_bridge_targets(
            events=event_tuple,
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
        event_tuple
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
            return (
                InputVatFulfillmentBridgeSourcePlan(
                    reversal_event_ids=(),
                    replacement_target=None,
                )
            )

        return (
            InputVatFulfillmentBridgeSourcePlan(
                reversal_event_ids=(),
                replacement_target=target,
            )
        )

    if (
        current.tax_calculation_id
        != target.tax_calculation_id
    ):
        raise (
            InputVatFulfillmentBridgeDataIntegrityError(
                "INPUT VAT fulfillment source "
                "tax_calculation_id changed "
                "unexpectedly"
            )
        )

    if (
        current.event_date
        != target.event_date
    ):
        raise (
            InputVatFulfillmentBridgeDataIntegrityError(
                "INPUT VAT fulfillment source "
                "event_date changed unexpectedly"
            )
        )

    if (
        _decimal(
            current.amount
        )
        == _decimal(
            target.amount
        )
    ):
        return (
            InputVatFulfillmentBridgeSourcePlan(
                reversal_event_ids=(),
                replacement_target=None,
            )
        )

    reversal_event_ids = tuple(
        event.id
        for event in active_source_events
    )

    if not reversal_event_ids:
        raise (
            InputVatFulfillmentBridgeDataIntegrityError(
                "INPUT VAT fulfillment bridge "
                "current target has no active "
                "persistent source event"
            )
        )

    if target.is_zero:
        return (
            InputVatFulfillmentBridgeSourcePlan(
                reversal_event_ids=(
                    reversal_event_ids
                ),
                replacement_target=None,
            )
        )

    return (
        InputVatFulfillmentBridgeSourcePlan(
            reversal_event_ids=(
                reversal_event_ids
            ),
            replacement_target=target,
        )
    )


async def _lock_input_vat_fulfillment_bridge_source(
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
        raise (
            InputVatFulfillmentBridgeSourceNotFoundError(
                "InvoiceFulfillmentAllocation "
                "INPUT VAT bridge source not found"
            )
        )

    return source


async def _lock_input_vat_fulfillment_bridge_tax_calculation(
    db: AsyncSession,
    *,
    company_id: int,
    tax_calculation_id: int,
) -> TaxCalculation:
    calculation = (
        await db.execute(
            select(
                TaxCalculation
            )
            .where(
                TaxCalculation.company_id
                == company_id,
                TaxCalculation.id
                == tax_calculation_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()

    if calculation is None:
        raise (
            InputVatFulfillmentBridgeTaxCalculationNotFoundError(
                "INPUT VAT fulfillment bridge "
                "TaxCalculation not found"
            )
        )

    return calculation


def _validate_source_tax_identity(
    *,
    source: InvoiceFulfillmentAllocation,
    calculation: TaxCalculation,
    target: InputVatFulfillmentBridgeTarget,
    currency_code: str,
) -> None:
    if (
        source.company_id
        != calculation.company_id
    ):
        raise (
            InputVatFulfillmentBridgeDataIntegrityError(
                "INPUT VAT bridge source and "
                "TaxCalculation company mismatch"
            )
        )

    if source.id != target.source_id:
        raise (
            InputVatFulfillmentBridgeDataIntegrityError(
                "Locked INPUT VAT bridge source "
                "does not match target source_id"
            )
        )

    if (
        calculation.id
        != target.tax_calculation_id
    ):
        raise (
            InputVatFulfillmentBridgeDataIntegrityError(
                "Locked INPUT VAT TaxCalculation "
                "does not match target"
            )
        )

    if (
        calculation.trade_document_id
        != source.invoice_id
    ):
        raise (
            InputVatFulfillmentBridgeDataIntegrityError(
                "INPUT VAT TaxCalculation does not "
                "belong to source Invoice"
            )
        )

    if (
        calculation.trade_document_line_id
        != source.invoice_line_id
    ):
        raise (
            InputVatFulfillmentBridgeDataIntegrityError(
                "INPUT VAT TaxCalculation does not "
                "belong to source Invoice line"
            )
        )

    if (
        calculation.product_id
        != source.product_id
    ):
        raise (
            InputVatFulfillmentBridgeDataIntegrityError(
                "INPUT VAT TaxCalculation product "
                "does not match fulfillment source"
            )
        )

    if calculation.tax_type != TaxType.VAT:
        raise (
            InputVatFulfillmentBridgeDataIntegrityError(
                "INPUT VAT fulfillment bridge "
                "requires a VAT TaxCalculation"
            )
        )

    if (
        calculation.direction
        != TaxDirection.INPUT
    ):
        raise (
            InputVatFulfillmentBridgeDataIntegrityError(
                "INPUT VAT fulfillment bridge "
                "requires an INPUT TaxCalculation"
            )
        )

    if (
        calculation.currency_code
        != currency_code
    ):
        raise (
            InputVatFulfillmentBridgeDataIntegrityError(
                "INPUT VAT TaxCalculation currency "
                "does not match reconciliation currency"
            )
        )

    if (
        target.currency_code
        != currency_code
    ):
        raise (
            InputVatFulfillmentBridgeDataIntegrityError(
                "INPUT VAT bridge target currency "
                "does not match reconciliation currency"
            )
        )


def _validate_source_state(
    *,
    source: InvoiceFulfillmentAllocation,
    target: InputVatFulfillmentBridgeTarget,
) -> None:
    try:
        status = (
            InvoiceFulfillmentAllocationStatus(
                source.status
            )
        )
    except ValueError as exc:
        raise (
            InputVatFulfillmentBridgeSourceStateError(
                "Unsupported fulfillment allocation status"
            )
        ) from exc

    if (
        not target.is_zero
        and status
        != InvoiceFulfillmentAllocationStatus.ACTIVE
    ):
        raise (
            InputVatFulfillmentBridgeSourceStateError(
                "Positive INPUT VAT fulfillment "
                "bridge target requires ACTIVE "
                "InvoiceFulfillmentAllocation"
            )
        )


async def _load_input_vat_fulfillment_bridge_events(
    db: AsyncSession,
    *,
    company_id: int,
    source_id: int,
    lock_rows: bool,
) -> tuple[
    InputVatFulfillmentBridgeEvent,
    ...,
]:
    statement = (
        select(
            InputVatFulfillmentBridgeEvent
        )
        .where(
            (
                InputVatFulfillmentBridgeEvent
                .company_id
                == company_id
            ),
            (
                InputVatFulfillmentBridgeEvent
                .invoice_fulfillment_allocation_id
                == source_id
            ),
        )
        .order_by(
            InputVatFulfillmentBridgeEvent.id
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


async def reconcile_input_vat_fulfillment_bridge_source(
    db: AsyncSession,
    *,
    company_id: int,
    target: InputVatFulfillmentBridgeTarget,
    currency_code: str,
    created_by: int,
    reversal_date: date | None = None,
) -> tuple[
    InputVatFulfillmentBridgeEvent,
    ...,
]:
    """
    Reconcile one fulfillment source to its complete desired
    economic INPUT VAT bridge state.

    Existing events are never updated or deleted.

    Lock order:

        InvoiceFulfillmentAllocation
        -> TaxCalculation
        -> InputVatFulfillmentBridgeEvent history

    Caller owns COMMIT / ROLLBACK.
    """

    if company_id <= 0:
        raise ValueError(
            "company_id must be greater than zero"
        )

    if created_by <= 0:
        raise ValueError(
            "created_by must be greater than zero"
        )

    _validate_target(
        target,
        currency_code=currency_code,
    )

    effective_reversal_date = (
        reversal_date
        if reversal_date is not None
        else target.event_date
    )

    if not isinstance(
        effective_reversal_date,
        date,
    ):
        raise (
            InputVatFulfillmentBridgeDataIntegrityError(
                "INPUT VAT fulfillment bridge "
                "reversal_date must be a date"
            )
        )

    source = (
        await _lock_input_vat_fulfillment_bridge_source(
            db,
            company_id=company_id,
            source_id=target.source_id,
        )
    )

    calculation = (
        await _lock_input_vat_fulfillment_bridge_tax_calculation(
            db,
            company_id=company_id,
            tax_calculation_id=(
                target.tax_calculation_id
            ),
        )
    )

    _validate_source_tax_identity(
        source=source,
        calculation=calculation,
        target=target,
        currency_code=currency_code,
    )

    _validate_source_state(
        source=source,
        target=target,
    )

    events = (
        await _load_input_vat_fulfillment_bridge_events(
            db,
            company_id=company_id,
            source_id=target.source_id,
            lock_rows=True,
        )
    )

    plan = (
        build_input_vat_fulfillment_bridge_source_plan(
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

    created: list[
        InputVatFulfillmentBridgeEvent
    ] = []

    for event_id in (
        plan.reversal_event_ids
    ):
        original = event_by_id.get(
            event_id
        )

        if original is None:
            raise (
                InputVatFulfillmentBridgeDataIntegrityError(
                    "INPUT VAT bridge event selected "
                    "for reversal does not exist"
                )
            )

        if (
            effective_reversal_date
            < original.bridge_date
        ):
            raise (
                InputVatFulfillmentBridgeDataIntegrityError(
                    "INPUT VAT bridge reversal_date "
                    "cannot precede original bridge_date"
                )
            )

        reversal = (
            InputVatFulfillmentBridgeEvent(
                company_id=company_id,
                tax_calculation_id=(
                    original.tax_calculation_id
                ),
                invoice_fulfillment_allocation_id=(
                    original
                    .invoice_fulfillment_allocation_id
                ),
                bridge_date=(
                    effective_reversal_date
                ),
                bridged_tax_amount=(
                    original.bridged_tax_amount
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
        if replacement.is_zero:
            raise (
                InputVatFulfillmentBridgeDataIntegrityError(
                    "Zero INPUT VAT bridge target "
                    "cannot be persisted as an "
                    "original event"
                )
            )

        original = (
            InputVatFulfillmentBridgeEvent(
                company_id=company_id,
                tax_calculation_id=(
                    replacement
                    .tax_calculation_id
                ),
                invoice_fulfillment_allocation_id=(
                    replacement.source_id
                ),
                bridge_date=(
                    replacement.event_date
                ),
                bridged_tax_amount=(
                    replacement.amount
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


async def get_persistent_input_vat_fulfillment_bridge_target(
    db: AsyncSession,
    *,
    company_id: int,
    source_id: int,
    currency_code: str,
) -> InputVatFulfillmentBridgeTarget | None:
    """Return current active persistent state without mutation."""

    if company_id <= 0:
        raise ValueError(
            "company_id must be greater than zero"
        )

    if source_id <= 0:
        raise ValueError(
            "source_id must be greater than zero"
        )

    events = (
        await _load_input_vat_fulfillment_bridge_events(
            db,
            company_id=company_id,
            source_id=source_id,
            lock_rows=False,
        )
    )

    targets = (
        build_current_input_vat_fulfillment_bridge_targets(
            events=events,
            currency_code=currency_code,
        )
    )

    matching = tuple(
        target
        for target in targets
        if target.source_id
        == source_id
    )

    if len(matching) > 1:
        raise (
            InputVatFulfillmentBridgeDataIntegrityError(
                "More than one active INPUT VAT "
                "bridge target exists for source"
            )
        )

    if not matching:
        return None

    return matching[0]
