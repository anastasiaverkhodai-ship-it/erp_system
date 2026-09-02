from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Iterable

from app.services.vat_advance_bridge_calculation_service import (
    VatAdvanceBridgeDataIntegrityError,
    VatAdvanceBridgeTarget,
)


ZERO = Decimal("0")


class VatAdvanceBridgePersistenceError(Exception):
    """Base persistent VAT advance bridge error."""


@dataclass(
    frozen=True,
    slots=True,
)
class VatAdvanceBridgeSourcePlan:
    """
    Immutable persistence action for one fulfillment bridge source.

    replacement_target is always the complete desired source state,
    never a monetary delta.

    New positive target:
        create one full original event.

    Exact current target:
        no-op.

    Changed positive amount:
        reverse current active original
        +
        create one full replacement.

    Zero target:
        reverse current active original only.
    """

    reversal_event_ids: tuple[int, ...]
    replacement_target: (
        VatAdvanceBridgeTarget
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
    """
    Return immutable originals that have not themselves been reversed.

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
            raise VatAdvanceBridgeDataIntegrityError(
                "Persistent original VAT advance bridge "
                "event must have a positive ID"
            )

        if event_id in reversed_ids:
            continue

        active.append(
            event
        )

    return tuple(
        active
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
        raise VatAdvanceBridgeDataIntegrityError(
            "VAT advance bridge currency_code "
            "must contain exactly 3 characters"
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
        raise VatAdvanceBridgeDataIntegrityError(
            "Active VAT advance bridge event ID "
            "must be greater than zero"
        )

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
        raise VatAdvanceBridgeDataIntegrityError(
            "Active VAT advance bridge event must "
            "have a valid tax calculation"
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
        raise VatAdvanceBridgeDataIntegrityError(
            "Active VAT advance bridge event must "
            "have a valid fulfillment source"
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
        raise VatAdvanceBridgeDataIntegrityError(
            "Active VAT advance bridge event must "
            "have a bridge date"
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
        raise VatAdvanceBridgeDataIntegrityError(
            "Active VAT advance bridge event "
            "currency does not match target"
        )

    amount = _decimal(
        getattr(
            event,
            "bridged_tax_amount",
            ZERO,
        )
    )

    if amount <= ZERO:
        raise VatAdvanceBridgeDataIntegrityError(
            "Active VAT advance bridge amount "
            "must be greater than zero"
        )


def _validate_target(
    target: VatAdvanceBridgeTarget,
    *,
    currency_code: str,
) -> None:
    if not isinstance(
        target,
        VatAdvanceBridgeTarget,
    ):
        raise VatAdvanceBridgeDataIntegrityError(
            "VAT advance bridge persistence target "
            "must be VatAdvanceBridgeTarget"
        )

    _validate_currency_code(
        currency_code
    )

    if (
        target.currency_code
        != currency_code
    ):
        raise VatAdvanceBridgeDataIntegrityError(
            "VAT advance bridge target currency "
            "does not match reconciliation currency"
        )

    if target.tax_calculation_id <= 0:
        raise VatAdvanceBridgeDataIntegrityError(
            "VAT advance bridge target "
            "tax_calculation_id must be greater than zero"
        )

    if target.source_id <= 0:
        raise VatAdvanceBridgeDataIntegrityError(
            "VAT advance bridge target source_id "
            "must be greater than zero"
        )

    if not isinstance(
        target.event_date,
        date,
    ):
        raise VatAdvanceBridgeDataIntegrityError(
            "VAT advance bridge target event_date "
            "must be a date"
        )

    amount = _decimal(
        target.amount
    )

    if amount < ZERO:
        raise VatAdvanceBridgeDataIntegrityError(
            "VAT advance bridge target amount "
            "cannot be negative"
        )


def build_current_vat_advance_bridge_targets(
    *,
    events: Iterable,
    currency_code: str,
) -> tuple[
    VatAdvanceBridgeTarget,
    ...,
]:
    """
    Rebuild current bridge targets from immutable event history.

    There may be at most one ACTIVE original for one
    InvoiceFulfillmentAllocation source.

    tax_calculation_id is immutable provenance for that source.
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
            raise VatAdvanceBridgeDataIntegrityError(
                "VAT advance bridge fulfillment source "
                "has more than one active original event"
            )

        by_source[
            source_id
        ] = event

    targets = []

    for source_id, event in by_source.items():
        targets.append(
            VatAdvanceBridgeTarget(
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


def build_vat_advance_bridge_source_plan(
    *,
    events: Iterable,
    target: VatAdvanceBridgeTarget,
    currency_code: str,
) -> VatAdvanceBridgeSourcePlan:
    """
    Plan immutable persistence for one fulfillment source.

    Source identity:
        InvoiceFulfillmentAllocation.id

    tax_calculation_id and event_date are immutable provenance.

    New positive source:
        create one full original.

    Exact current state:
        no-op.

    Amount mismatch:
        reverse current active original,
        then create one full replacement.

    Zero target:
        reverse current active original only.
    """

    _validate_target(
        target,
        currency_code=currency_code,
    )

    current_targets = (
        build_current_vat_advance_bridge_targets(
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
            return VatAdvanceBridgeSourcePlan(
                reversal_event_ids=(),
                replacement_target=None,
            )

        return VatAdvanceBridgeSourcePlan(
            reversal_event_ids=(),
            replacement_target=target,
        )

    if (
        current.tax_calculation_id
        != target.tax_calculation_id
    ):
        raise VatAdvanceBridgeDataIntegrityError(
            "VAT advance bridge fulfillment source "
            "tax_calculation_id changed unexpectedly"
        )

    if (
        current.event_date
        != target.event_date
    ):
        raise VatAdvanceBridgeDataIntegrityError(
            "VAT advance bridge source event_date "
            "changed unexpectedly"
        )

    if (
        _decimal(
            current.amount
        )
        == _decimal(
            target.amount
        )
    ):
        return VatAdvanceBridgeSourcePlan(
            reversal_event_ids=(),
            replacement_target=None,
        )

    reversal_event_ids = tuple(
        event.id
        for event in active_source_events
    )

    if not reversal_event_ids:
        raise VatAdvanceBridgeDataIntegrityError(
            "VAT advance bridge current target "
            "has no active persistent source event"
        )

    if target.is_zero:
        return VatAdvanceBridgeSourcePlan(
            reversal_event_ids=(
                reversal_event_ids
            ),
            replacement_target=None,
        )

    return VatAdvanceBridgeSourcePlan(
        reversal_event_ids=(
            reversal_event_ids
        ),
        replacement_target=target,
    )


from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.invoice_fulfillment_allocation import (
    InvoiceFulfillmentAllocation,
)
from app.models.tax_calculation import TaxCalculation
from app.models.vat_advance_bridge_event import (
    VatAdvanceBridgeEvent,
)
from app.services.tax_types import (
    TaxDirection,
    TaxType,
)


class VatAdvanceBridgeSourceNotFoundError(
    VatAdvanceBridgePersistenceError
):
    """Invoice fulfillment bridge source was not found."""


class VatAdvanceBridgeTaxCalculationNotFoundError(
    VatAdvanceBridgePersistenceError
):
    """Required immutable VAT calculation was not found."""


async def _lock_vat_advance_bridge_source(
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
        raise VatAdvanceBridgeSourceNotFoundError(
            "InvoiceFulfillmentAllocation "
            "VAT advance bridge source not found"
        )

    return source


async def _lock_vat_advance_bridge_tax_calculation(
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
            VatAdvanceBridgeTaxCalculationNotFoundError(
                "VAT advance bridge TaxCalculation "
                "not found"
            )
        )

    return calculation


def _validate_bridge_source_tax_identity(
    *,
    source: InvoiceFulfillmentAllocation,
    calculation: TaxCalculation,
    target: VatAdvanceBridgeTarget,
    currency_code: str,
) -> None:
    """
    Fail closed unless the immutable VAT calculation belongs to the
    exact commercial Invoice line represented by the fulfillment
    allocation.

    Allocation:
        invoice_id
        invoice_line_id
        product_id

    TaxCalculation:
        trade_document_id
        trade_document_line_id
        product_id
    """

    if source.company_id != calculation.company_id:
        raise VatAdvanceBridgeDataIntegrityError(
            "VAT advance bridge source and "
            "TaxCalculation company mismatch"
        )

    if source.id != target.source_id:
        raise VatAdvanceBridgeDataIntegrityError(
            "VAT advance bridge locked source "
            "does not match target source_id"
        )

    if calculation.id != target.tax_calculation_id:
        raise VatAdvanceBridgeDataIntegrityError(
            "VAT advance bridge locked TaxCalculation "
            "does not match target"
        )

    if (
        calculation.trade_document_id
        != source.invoice_id
    ):
        raise VatAdvanceBridgeDataIntegrityError(
            "VAT advance bridge TaxCalculation "
            "does not belong to source Invoice"
        )

    if (
        calculation.trade_document_line_id
        != source.invoice_line_id
    ):
        raise VatAdvanceBridgeDataIntegrityError(
            "VAT advance bridge TaxCalculation "
            "does not belong to source Invoice line"
        )

    if (
        calculation.product_id
        != source.product_id
    ):
        raise VatAdvanceBridgeDataIntegrityError(
            "VAT advance bridge TaxCalculation "
            "product does not match fulfillment source"
        )

    if calculation.tax_type != TaxType.VAT:
        raise VatAdvanceBridgeDataIntegrityError(
            "VAT advance bridge requires "
            "a VAT TaxCalculation"
        )

    if calculation.direction != TaxDirection.OUTPUT:
        raise VatAdvanceBridgeDataIntegrityError(
            "VAT advance bridge requires "
            "an OUTPUT VAT TaxCalculation"
        )

    if (
        calculation.currency_code
        != currency_code
    ):
        raise VatAdvanceBridgeDataIntegrityError(
            "VAT advance bridge TaxCalculation "
            "currency does not match reconciliation currency"
        )

    if target.currency_code != currency_code:
        raise VatAdvanceBridgeDataIntegrityError(
            "VAT advance bridge target currency "
            "does not match reconciliation currency"
        )


async def _load_vat_advance_bridge_events(
    db: AsyncSession,
    *,
    company_id: int,
    source_id: int,
    lock_rows: bool,
) -> tuple[
    VatAdvanceBridgeEvent,
    ...,
]:
    """
    Load complete immutable history for one fulfillment source.

    History is loaded by fulfillment source, rather than by current
    tax_calculation_id, so inconsistent duplicate provenance cannot be
    silently hidden from the persistence planner.
    """

    statement = (
        select(
            VatAdvanceBridgeEvent
        )
        .where(
            VatAdvanceBridgeEvent.company_id
            == company_id,
            (
                VatAdvanceBridgeEvent
                .invoice_fulfillment_allocation_id
                == source_id
            ),
        )
        .order_by(
            VatAdvanceBridgeEvent.id
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


async def reconcile_vat_advance_bridge_source(
    db: AsyncSession,
    *,
    company_id: int,
    target: VatAdvanceBridgeTarget,
    currency_code: str,
    created_by: int,
    reversal_date: date | None = None,
) -> tuple[
    VatAdvanceBridgeEvent,
    ...,
]:
    """
    Reconcile one InvoiceFulfillmentAllocation source to its complete
    desired VAT advance bridge state.

    Existing bridge rows are never updated or deleted.

    Persistence semantics:

        new positive source
            -> one immutable original

        exact target
            -> no-op

        changed positive target
            -> immutable reversal
            -> one full replacement original

        zero target
            -> immutable reversal only

    Lock order:

        InvoiceFulfillmentAllocation
        -> TaxCalculation
        -> VatAdvanceBridgeEvent history

    Caller owns transaction COMMIT / ROLLBACK.
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
        VatAdvanceBridgeTarget,
    ):
        raise VatAdvanceBridgeDataIntegrityError(
            "target must be VatAdvanceBridgeTarget"
        )

    if target.source_id <= 0:
        raise VatAdvanceBridgeDataIntegrityError(
            "VAT advance bridge target source_id "
            "must be greater than zero"
        )

    if target.tax_calculation_id <= 0:
        raise VatAdvanceBridgeDataIntegrityError(
            "VAT advance bridge target "
            "tax_calculation_id must be greater than zero"
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
        raise VatAdvanceBridgeDataIntegrityError(
            "VAT advance bridge reversal_date "
            "must be a date"
        )

    source = (
        await _lock_vat_advance_bridge_source(
            db,
            company_id=company_id,
            source_id=target.source_id,
        )
    )

    calculation = (
        await _lock_vat_advance_bridge_tax_calculation(
            db,
            company_id=company_id,
            tax_calculation_id=(
                target.tax_calculation_id
            ),
        )
    )

    _validate_bridge_source_tax_identity(
        source=source,
        calculation=calculation,
        target=target,
        currency_code=currency_code,
    )

    events = (
        await _load_vat_advance_bridge_events(
            db,
            company_id=company_id,
            source_id=target.source_id,
            lock_rows=True,
        )
    )

    plan = (
        build_vat_advance_bridge_source_plan(
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
        VatAdvanceBridgeEvent
    ] = []

    for event_id in (
        plan.reversal_event_ids
    ):
        original = event_by_id.get(
            event_id
        )

        if original is None:
            raise VatAdvanceBridgeDataIntegrityError(
                "VAT advance bridge event selected "
                "for reversal does not exist"
            )

        reversal = VatAdvanceBridgeEvent(
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
            raise VatAdvanceBridgeDataIntegrityError(
                "Zero VAT advance bridge target "
                "cannot be persisted as an original event"
            )

        original = VatAdvanceBridgeEvent(
            company_id=company_id,
            tax_calculation_id=(
                replacement.tax_calculation_id
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


async def get_persistent_vat_advance_bridge_target(
    db: AsyncSession,
    *,
    company_id: int,
    source_id: int,
    currency_code: str,
) -> VatAdvanceBridgeTarget | None:
    """
    Return current persistent bridge state for one fulfillment source
    without mutating immutable history.
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
        await _load_vat_advance_bridge_events(
            db,
            company_id=company_id,
            source_id=source_id,
            lock_rows=False,
        )
    )

    targets = (
        build_current_vat_advance_bridge_targets(
            events=events,
            currency_code=currency_code,
        )
    )

    if not targets:
        return None

    if len(targets) != 1:
        raise VatAdvanceBridgeDataIntegrityError(
            "VAT advance bridge source resolved "
            "to more than one current target"
        )

    target = targets[0]

    if target.source_id != source_id:
        raise VatAdvanceBridgeDataIntegrityError(
            "VAT advance bridge current target "
            "source identity mismatch"
        )

    return target
