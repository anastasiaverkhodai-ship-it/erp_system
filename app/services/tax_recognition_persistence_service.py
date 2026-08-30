from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tax_calculation import (
    TaxCalculation,
)
from app.models.tax_recognition_event import (
    TaxRecognitionEvent,
)
from app.services.tax_recognition_types import (
    TaxRecognitionMethod,
)
from app.services.tax_types import (
    TaxDirection,
)


class TaxRecognitionPersistenceError(Exception):
    """Base persistent tax recognition error."""


class TaxRecognitionCalculationNotFoundError(
    TaxRecognitionPersistenceError
):
    """TaxCalculation does not exist."""


class TaxRecognitionSourceError(
    TaxRecognitionPersistenceError
):
    """Typed recognition source is invalid."""


class TaxRecognitionSourceMethodError(
    TaxRecognitionSourceError
):
    """Recognition source is incompatible with method."""


class TaxRecognitionInputEvidenceRequiredError(
    TaxRecognitionPersistenceError
):
    """
    INPUT VAT requires separate tax-credit evidence.
    """


class TaxRecognitionOverRecognitionError(
    TaxRecognitionPersistenceError
):
    """Net recognition would exceed TaxCalculation."""


class TaxRecognitionTargetError(
    TaxRecognitionPersistenceError
):
    """Recognition target is invalid."""


class TaxRecognitionDataIntegrityError(
    TaxRecognitionPersistenceError
):
    """Persistent recognition data is inconsistent."""


@dataclass(
    frozen=True,
    slots=True,
)
class TaxRecognitionNet:
    taxable_base: Decimal
    tax_amount: Decimal


@dataclass(
    frozen=True,
    slots=True,
)
class TaxRecognitionSourcePlan:
    reversal_event_ids: tuple[int, ...]
    increment_taxable_base: Decimal
    increment_tax_amount: Decimal

    @property
    def is_noop(self) -> bool:
        return (
            not self.reversal_event_ids
            and self.increment_taxable_base
            == Decimal("0")
            and self.increment_tax_amount
            == Decimal("0")
        )


def _decimal(
    value,
) -> Decimal:
    return Decimal(value)


def _validate_source(
    *,
    invoice_fulfillment_allocation_id: int | None,
    payment_settlement_allocation_id: int | None,
) -> None:
    fulfillment_selected = (
        invoice_fulfillment_allocation_id
        is not None
    )

    settlement_selected = (
        payment_settlement_allocation_id
        is not None
    )

    if (
        fulfillment_selected
        == settlement_selected
    ):
        raise TaxRecognitionSourceError(
            "Exactly one typed recognition "
            "source must be provided"
        )

    if (
        invoice_fulfillment_allocation_id
        is not None
        and invoice_fulfillment_allocation_id
        <= 0
    ):
        raise TaxRecognitionSourceError(
            "invoice_fulfillment_allocation_id "
            "must be greater than zero"
        )

    if (
        payment_settlement_allocation_id
        is not None
        and payment_settlement_allocation_id
        <= 0
    ):
        raise TaxRecognitionSourceError(
            "payment_settlement_allocation_id "
            "must be greater than zero"
        )


def _matches_source(
    event,
    *,
    invoice_fulfillment_allocation_id: int | None,
    payment_settlement_allocation_id: int | None,
) -> bool:
    return (
        getattr(
            event,
            "invoice_fulfillment_allocation_id",
            None,
        )
        == invoice_fulfillment_allocation_id
        and getattr(
            event,
            "payment_settlement_allocation_id",
            None,
        )
        == payment_settlement_allocation_id
    )


def _active_original_events(
    events: Iterable,
) -> tuple:
    event_tuple = tuple(events)

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

    return tuple(
        event
        for event in event_tuple
        if (
            getattr(
                event,
                "reversal_of_id",
                None,
            )
            is None
            and getattr(
                event,
                "id",
                None,
            )
            not in reversed_ids
        )
    )


def calculate_tax_recognition_net(
    events: Iterable,
) -> TaxRecognitionNet:
    active = _active_original_events(
        events
    )

    return TaxRecognitionNet(
        taxable_base=sum(
            (
                _decimal(
                    event.recognized_taxable_base
                )
                for event in active
            ),
            Decimal("0"),
        ),
        tax_amount=sum(
            (
                _decimal(
                    event.recognized_tax_amount
                )
                for event in active
            ),
            Decimal("0"),
        ),
    )


def build_tax_recognition_source_plan(
    *,
    calculation,
    events: Iterable,
    target_taxable_base: Decimal,
    target_tax_amount: Decimal,
    invoice_fulfillment_allocation_id: int | None = None,
    payment_settlement_allocation_id: int | None = None,
) -> TaxRecognitionSourcePlan:
    _validate_source(
        invoice_fulfillment_allocation_id=(
            invoice_fulfillment_allocation_id
        ),
        payment_settlement_allocation_id=(
            payment_settlement_allocation_id
        ),
    )

    try:
        direction = TaxDirection(
            calculation.direction
        )
    except ValueError as exc:
        raise TaxRecognitionDataIntegrityError(
            "Unsupported TaxCalculation direction"
        ) from exc

    if direction != TaxDirection.OUTPUT:
        raise (
            TaxRecognitionInputEvidenceRequiredError(
                "Automatic INPUT VAT recognition "
                "requires tax-credit evidence"
            )
        )

    try:
        method = TaxRecognitionMethod(
            calculation.recognition_method
        )
    except ValueError as exc:
        raise TaxRecognitionDataIntegrityError(
            "Unsupported recognition method"
        ) from exc

    if (
        method
        == TaxRecognitionMethod.MANUAL
    ):
        raise TaxRecognitionSourceMethodError(
            "MANUAL recognition cannot use "
            "an automatic typed source"
        )

    if (
        method
        == TaxRecognitionMethod.CASH_METHOD
        and invoice_fulfillment_allocation_id
        is not None
    ):
        raise TaxRecognitionSourceMethodError(
            "CASH_METHOD recognition requires "
            "payment settlement source"
        )

    target_base = _decimal(
        target_taxable_base
    )

    target_tax = _decimal(
        target_tax_amount
    )

    if (
        target_base < 0
        or target_tax < 0
    ):
        raise TaxRecognitionTargetError(
            "Recognition target cannot be negative"
        )

    calculated_base = _decimal(
        calculation.taxable_base
    )

    calculated_tax = _decimal(
        calculation.tax_amount
    )

    if (
        target_base > calculated_base
        or target_tax > calculated_tax
    ):
        raise TaxRecognitionOverRecognitionError(
            "Recognition source target exceeds "
            "TaxCalculation"
        )

    active = _active_original_events(
        events
    )

    source_active = tuple(
        event
        for event in active
        if _matches_source(
            event,
            invoice_fulfillment_allocation_id=(
                invoice_fulfillment_allocation_id
            ),
            payment_settlement_allocation_id=(
                payment_settlement_allocation_id
            ),
        )
    )

    current_source_base = sum(
        (
            _decimal(
                event.recognized_taxable_base
            )
            for event in source_active
        ),
        Decimal("0"),
    )

    current_source_tax = sum(
        (
            _decimal(
                event.recognized_tax_amount
            )
            for event in source_active
        ),
        Decimal("0"),
    )

    total_net = (
        calculate_tax_recognition_net(
            active
        )
    )

    total_after_base = (
        total_net.taxable_base
        - current_source_base
        + target_base
    )

    total_after_tax = (
        total_net.tax_amount
        - current_source_tax
        + target_tax
    )

    if (
        total_after_base > calculated_base
        or total_after_tax > calculated_tax
    ):
        raise TaxRecognitionOverRecognitionError(
            "Recognition total after source "
            "reconciliation exceeds "
            "TaxCalculation"
        )

    if (
        target_base
        == current_source_base
        and target_tax
        == current_source_tax
    ):
        return TaxRecognitionSourcePlan(
            reversal_event_ids=(),
            increment_taxable_base=Decimal(
                "0"
            ),
            increment_tax_amount=Decimal(
                "0"
            ),
        )

    monotonic_increase = (
        target_base
        >= current_source_base
        and target_tax
        >= current_source_tax
    )

    if monotonic_increase:
        return TaxRecognitionSourcePlan(
            reversal_event_ids=(),
            increment_taxable_base=(
                target_base
                - current_source_base
            ),
            increment_tax_amount=(
                target_tax
                - current_source_tax
            ),
        )

    reversal_ids = []

    for event in source_active:
        event_id = getattr(
            event,
            "id",
            None,
        )

        if event_id is None:
            raise TaxRecognitionDataIntegrityError(
                "Persistent recognition event "
                "must have an ID"
            )

        reversal_ids.append(
            int(event_id)
        )

    return TaxRecognitionSourcePlan(
        reversal_event_ids=tuple(
            sorted(
                reversal_ids
            )
        ),
        increment_taxable_base=(
            target_base
        ),
        increment_tax_amount=(
            target_tax
        ),
    )


async def _lock_tax_calculation(
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
            TaxRecognitionCalculationNotFoundError(
                "TaxCalculation not found"
            )
        )

    return calculation


async def _load_events(
    db: AsyncSession,
    *,
    company_id: int,
    tax_calculation_id: int,
    lock_rows: bool,
) -> tuple[TaxRecognitionEvent, ...]:
    statement = (
        select(
            TaxRecognitionEvent
        )
        .where(
            TaxRecognitionEvent.company_id
            == company_id,
            TaxRecognitionEvent.tax_calculation_id
            == tax_calculation_id,
        )
        .order_by(
            TaxRecognitionEvent.id
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


async def reconcile_output_tax_recognition_source(
    db: AsyncSession,
    *,
    company_id: int,
    tax_calculation_id: int,
    recognition_date: date,
    target_taxable_base: Decimal,
    target_tax_amount: Decimal,
    created_by: int,
    reversal_date: date | None = None,
    invoice_fulfillment_allocation_id: int | None = None,
    payment_settlement_allocation_id: int | None = None,
) -> tuple[TaxRecognitionEvent, ...]:
    if company_id <= 0:
        raise ValueError(
            "company_id must be greater than zero"
        )

    if tax_calculation_id <= 0:
        raise ValueError(
            "tax_calculation_id must be greater than zero"
        )

    if created_by <= 0:
        raise ValueError(
            "created_by must be greater than zero"
        )

    effective_reversal_date = (
        reversal_date
        if reversal_date is not None
        else recognition_date
    )

    calculation = await _lock_tax_calculation(
        db,
        company_id=company_id,
        tax_calculation_id=(
            tax_calculation_id
        ),
    )

    events = await _load_events(
        db,
        company_id=company_id,
        tax_calculation_id=(
            tax_calculation_id
        ),
        lock_rows=True,
    )

    plan = build_tax_recognition_source_plan(
        calculation=calculation,
        events=events,
        target_taxable_base=(
            target_taxable_base
        ),
        target_tax_amount=(
            target_tax_amount
        ),
        invoice_fulfillment_allocation_id=(
            invoice_fulfillment_allocation_id
        ),
        payment_settlement_allocation_id=(
            payment_settlement_allocation_id
        ),
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
            raise TaxRecognitionDataIntegrityError(
                "Recognition event selected "
                "for reversal does not exist"
            )

        reversal = TaxRecognitionEvent(
            company_id=company_id,
            tax_calculation_id=(
                tax_calculation_id
            ),
            invoice_fulfillment_allocation_id=(
                original
                .invoice_fulfillment_allocation_id
            ),
            payment_settlement_allocation_id=(
                original
                .payment_settlement_allocation_id
            ),
            recognition_date=(
                effective_reversal_date
            ),
            recognized_taxable_base=(
                original
                .recognized_taxable_base
            ),
            recognized_tax_amount=(
                original
                .recognized_tax_amount
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

    if (
        plan.increment_taxable_base
        > Decimal("0")
        or plan.increment_tax_amount
        > Decimal("0")
    ):
        increment = TaxRecognitionEvent(
            company_id=company_id,
            tax_calculation_id=(
                tax_calculation_id
            ),
            invoice_fulfillment_allocation_id=(
                invoice_fulfillment_allocation_id
            ),
            payment_settlement_allocation_id=(
                payment_settlement_allocation_id
            ),
            recognition_date=(
                recognition_date
            ),
            recognized_taxable_base=(
                plan.increment_taxable_base
            ),
            recognized_tax_amount=(
                plan.increment_tax_amount
            ),
            currency_code=(
                calculation.currency_code
            ),
            created_by=created_by,
            reversal_of_id=None,
        )

        db.add(
            increment
        )

        created.append(
            increment
        )

    await db.flush()

    return tuple(
        created
    )


async def get_persistent_tax_recognition_net(
    db: AsyncSession,
    *,
    company_id: int,
    tax_calculation_id: int,
) -> TaxRecognitionNet:
    events = await _load_events(
        db,
        company_id=company_id,
        tax_calculation_id=(
            tax_calculation_id
        ),
        lock_rows=False,
    )

    return calculate_tax_recognition_net(
        events
    )
