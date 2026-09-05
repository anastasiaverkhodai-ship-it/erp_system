from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.purchase_return_vat_adjustment_event import (
    PurchaseReturnVatAdjustmentEvent,
)
from app.services.purchase_return_vat_adjustment_calculation_service import (
    PurchaseReturnVatAdjustmentDataIntegrityError,
    PurchaseReturnVatAdjustmentTarget,
    build_purchase_return_vat_adjustment_target,
)


class PurchaseReturnVatAdjustmentPersistenceError(
    Exception
):
    """Base immutable Purchase Return VAT persistence error."""


class PurchaseReturnVatAdjustmentPersistenceDataIntegrityError(
    PurchaseReturnVatAdjustmentPersistenceError
):
    """Persisted VAT adjustment history is inconsistent."""


@dataclass(
    frozen=True,
    slots=True,
)
class PurchaseReturnVatAdjustmentSourcePlan:
    """
    Immutable persistence plan for one VAT-adjustment source.

    New positive target:
        create one original.

    Exact active target:
        no-op.

    Changed positive target:
        reverse the active original
        then create one full replacement.

    Zero target:
        reverse the active original only.

    replacement_target is always the full desired state, never a delta.
    """

    reversal_event_ids: tuple[
        int,
        ...,
    ]
    replacement_target: (
        PurchaseReturnVatAdjustmentTarget
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
    *,
    field: str,
) -> Decimal:
    result = Decimal(
        str(value)
    )

    if not result.is_finite():
        raise (
            PurchaseReturnVatAdjustmentPersistenceDataIntegrityError(
                f"{field} must be finite"
            )
        )

    return result


def _normalized_target(
    target: PurchaseReturnVatAdjustmentTarget,
) -> PurchaseReturnVatAdjustmentTarget:
    if not isinstance(
        target,
        PurchaseReturnVatAdjustmentTarget,
    ):
        raise (
            PurchaseReturnVatAdjustmentPersistenceDataIntegrityError(
                "target must be "
                "PurchaseReturnVatAdjustmentTarget"
            )
        )

    try:
        return (
            build_purchase_return_vat_adjustment_target(
                purchase_return_recognition_event_id=(
                    target
                    .purchase_return_recognition_event_id
                ),
                tax_calculation_id=(
                    target.tax_calculation_id
                ),
                adjustment_date=(
                    target.adjustment_date
                ),
                basis_kind=(
                    target.basis_kind
                ),
                adjusted_taxable_base=(
                    target.adjusted_taxable_base
                ),
                adjusted_tax_amount=(
                    target.adjusted_tax_amount
                ),
                currency_code=(
                    target.currency_code
                ),
            )
        )
    except (
        PurchaseReturnVatAdjustmentDataIntegrityError
    ) as exc:
        raise (
            PurchaseReturnVatAdjustmentPersistenceDataIntegrityError(
                str(
                    exc
                )
            )
        ) from exc


def _event_id(
    event: PurchaseReturnVatAdjustmentEvent,
) -> int:
    value = event.id

    if (
        value is None
        or value <= 0
    ):
        raise (
            PurchaseReturnVatAdjustmentPersistenceDataIntegrityError(
                "Persisted VAT adjustment event "
                "must have a positive id"
            )
        )

    return value


def _validate_event_source(
    *,
    event: PurchaseReturnVatAdjustmentEvent,
    target: PurchaseReturnVatAdjustmentTarget,
) -> None:
    if (
        event.purchase_return_recognition_event_id
        != target.purchase_return_recognition_event_id
        or event.tax_calculation_id
        != target.tax_calculation_id
        or event.basis_kind
        != target.basis_kind
    ):
        raise (
            PurchaseReturnVatAdjustmentPersistenceDataIntegrityError(
                "VAT adjustment history contains "
                "a different source identity"
            )
        )

    if (
        event.currency_code
        != target.currency_code
    ):
        raise (
            PurchaseReturnVatAdjustmentPersistenceDataIntegrityError(
                "VAT adjustment history currency "
                "does not match target"
            )
        )

    base = _decimal(
        event.adjusted_taxable_base,
        field="event adjusted_taxable_base",
    )
    tax = _decimal(
        event.adjusted_tax_amount,
        field="event adjusted_tax_amount",
    )

    if (
        base < 0
        or tax < 0
        or (
            base == 0
            and tax == 0
        )
    ):
        raise (
            PurchaseReturnVatAdjustmentPersistenceDataIntegrityError(
                "Persisted VAT adjustment event "
                "must contain nonzero nonnegative amounts"
            )
        )

    if not isinstance(
        event.adjustment_date,
        date,
    ):
        raise (
            PurchaseReturnVatAdjustmentPersistenceDataIntegrityError(
                "Persisted VAT adjustment date "
                "must be a date"
            )
        )


def _active_original_events(
    *,
    events: Iterable[
        PurchaseReturnVatAdjustmentEvent
    ],
    target: PurchaseReturnVatAdjustmentTarget,
) -> tuple[
    PurchaseReturnVatAdjustmentEvent,
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
                PurchaseReturnVatAdjustmentPersistenceDataIntegrityError(
                    "Duplicate VAT adjustment event id "
                    "in source history"
                )
            )

        event_by_id[
            event_id
        ] = event

    reversed_original_ids = set()

    for event in rows:
        if event.reversal_of_id is None:
            continue

        original = event_by_id.get(
            event.reversal_of_id
        )

        if original is None:
            raise (
                PurchaseReturnVatAdjustmentPersistenceDataIntegrityError(
                    "VAT adjustment reversal references "
                    "an event outside source history"
                )
            )

        if original.reversal_of_id is not None:
            raise (
                PurchaseReturnVatAdjustmentPersistenceDataIntegrityError(
                    "VAT adjustment reversal cannot "
                    "reverse another reversal"
                )
            )

        if event.reversal_of_id in reversed_original_ids:
            raise (
                PurchaseReturnVatAdjustmentPersistenceDataIntegrityError(
                    "VAT adjustment original has "
                    "multiple reversal rows"
                )
            )

        reversed_original_ids.add(
            event.reversal_of_id
        )

    active = tuple(
        event
        for event in rows
        if (
            event.reversal_of_id is None
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
            PurchaseReturnVatAdjustmentPersistenceDataIntegrityError(
                "VAT adjustment source has "
                "multiple active originals"
            )
        )

    return active


def _same_state(
    *,
    event: PurchaseReturnVatAdjustmentEvent,
    target: PurchaseReturnVatAdjustmentTarget,
) -> bool:
    return (
        event.adjustment_date
        == target.adjustment_date
        and _decimal(
            event.adjusted_taxable_base,
            field="event adjusted_taxable_base",
        )
        == target.adjusted_taxable_base
        and _decimal(
            event.adjusted_tax_amount,
            field="event adjusted_tax_amount",
        )
        == target.adjusted_tax_amount
        and event.currency_code
        == target.currency_code
    )


def build_purchase_return_vat_adjustment_source_plan(
    *,
    events: Iterable[
        PurchaseReturnVatAdjustmentEvent
    ],
    target: PurchaseReturnVatAdjustmentTarget,
) -> PurchaseReturnVatAdjustmentSourcePlan:
    """
    Build an immutable original / reversal / replacement plan.

    Source identity never mutates:
        PurchaseReturnRecognitionEvent
        TaxCalculation
        basis_kind
    """

    target = _normalized_target(
        target
    )

    active = _active_original_events(
        events=events,
        target=target,
    )

    if not active:
        if target.is_zero:
            return (
                PurchaseReturnVatAdjustmentSourcePlan(
                    reversal_event_ids=(),
                    replacement_target=None,
                )
            )

        return PurchaseReturnVatAdjustmentSourcePlan(
            reversal_event_ids=(),
            replacement_target=target,
        )

    current = active[
        0
    ]

    if (
        not target.is_zero
        and _same_state(
            event=current,
            target=target,
        )
    ):
        return (
            PurchaseReturnVatAdjustmentSourcePlan(
                reversal_event_ids=(),
                replacement_target=None,
            )
        )

    reversal_ids = (
        _event_id(
            current
        ),
    )

    if target.is_zero:
        return (
            PurchaseReturnVatAdjustmentSourcePlan(
                reversal_event_ids=(
                    reversal_ids
                ),
                replacement_target=None,
            )
        )

    return PurchaseReturnVatAdjustmentSourcePlan(
        reversal_event_ids=(
            reversal_ids
        ),
        replacement_target=(
            target
        ),
    )


async def _load_source_history(
    db: AsyncSession,
    *,
    company_id: int,
    target: PurchaseReturnVatAdjustmentTarget,
) -> tuple[
    PurchaseReturnVatAdjustmentEvent,
    ...,
]:
    statement = (
        select(
            PurchaseReturnVatAdjustmentEvent
        )
        .where(
            (
                PurchaseReturnVatAdjustmentEvent
                .company_id
                == company_id
            ),
            (
                PurchaseReturnVatAdjustmentEvent
                .purchase_return_recognition_event_id
                == (
                    target
                    .purchase_return_recognition_event_id
                )
            ),
            (
                PurchaseReturnVatAdjustmentEvent
                .tax_calculation_id
                == target.tax_calculation_id
            ),
            (
                PurchaseReturnVatAdjustmentEvent
                .basis_kind
                == target.basis_kind
            ),
        )
        .order_by(
            PurchaseReturnVatAdjustmentEvent.id
        )
        .with_for_update()
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


async def reconcile_purchase_return_vat_adjustment_source(
    db: AsyncSession,
    *,
    company_id: int,
    target: PurchaseReturnVatAdjustmentTarget,
    created_by: int,
    reversal_date: date | None = None,
) -> tuple[
    PurchaseReturnVatAdjustmentEvent,
    ...,
]:
    """
    Persist one complete desired VAT-adjustment source state.

    Historical rows are never UPDATEd or DELETEd.
    Caller owns commit / rollback.
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
        raise (
            PurchaseReturnVatAdjustmentPersistenceDataIntegrityError(
                "company_id must be a positive integer"
            )
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
        raise (
            PurchaseReturnVatAdjustmentPersistenceDataIntegrityError(
                "created_by must be a positive integer"
            )
        )

    target = _normalized_target(
        target
    )

    effective_reversal_date = (
        reversal_date
        if reversal_date is not None
        else target.adjustment_date
    )

    if not isinstance(
        effective_reversal_date,
        date,
    ):
        raise (
            PurchaseReturnVatAdjustmentPersistenceDataIntegrityError(
                "reversal_date must be a date"
            )
        )

    history = await _load_source_history(
        db,
        company_id=company_id,
        target=target,
    )

    plan = (
        build_purchase_return_vat_adjustment_source_plan(
            events=history,
            target=target,
        )
    )

    event_by_id = {
        _event_id(
            event
        ): event
        for event in history
    }

    created = []

    for event_id in plan.reversal_event_ids:
        original = event_by_id.get(
            event_id
        )

        if original is None:
            raise (
                PurchaseReturnVatAdjustmentPersistenceDataIntegrityError(
                    "VAT adjustment original selected "
                    "for reversal does not exist"
                )
            )

        if (
            effective_reversal_date
            < original.adjustment_date
        ):
            raise (
                PurchaseReturnVatAdjustmentPersistenceDataIntegrityError(
                    "reversal_date cannot precede "
                    "original adjustment_date"
                )
            )

        reversal = (
            PurchaseReturnVatAdjustmentEvent(
                company_id=company_id,
                purchase_return_recognition_event_id=(
                    original
                    .purchase_return_recognition_event_id
                ),
                tax_calculation_id=(
                    original.tax_calculation_id
                ),
                adjustment_date=(
                    effective_reversal_date
                ),
                basis_kind=(
                    original.basis_kind
                ),
                adjusted_taxable_base=(
                    original.adjusted_taxable_base
                ),
                adjusted_tax_amount=(
                    original.adjusted_tax_amount
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
        if replacement.is_zero:
            raise (
                PurchaseReturnVatAdjustmentPersistenceDataIntegrityError(
                    "Zero VAT adjustment target cannot "
                    "be persisted as an original event"
                )
            )

        original = (
            PurchaseReturnVatAdjustmentEvent(
                company_id=company_id,
                purchase_return_recognition_event_id=(
                    replacement
                    .purchase_return_recognition_event_id
                ),
                tax_calculation_id=(
                    replacement.tax_calculation_id
                ),
                adjustment_date=(
                    replacement.adjustment_date
                ),
                basis_kind=(
                    replacement.basis_kind
                ),
                adjusted_taxable_base=(
                    replacement.adjusted_taxable_base
                ),
                adjusted_tax_amount=(
                    replacement.adjusted_tax_amount
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
