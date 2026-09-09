from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.purchase_value_correction_vat_adjustment_event import (
    PurchaseValueCorrectionVatAdjustmentEvent,
)
from app.services.purchase_value_correction_vat_adjustment_calculation_service import (
    PurchaseValueCorrectionVatAdjustmentTarget,
)


ZERO = Decimal("0")


class PurchaseValueCorrectionVatAdjustmentPersistenceError(Exception):
    """Base immutable PVC VAT persistence error."""


class PurchaseValueCorrectionVatAdjustmentPersistenceStateError(
    PurchaseValueCorrectionVatAdjustmentPersistenceError
):
    """Desired PVC VAT state is invalid."""


class PurchaseValueCorrectionVatAdjustmentPersistenceIntegrityError(
    PurchaseValueCorrectionVatAdjustmentPersistenceError
):
    """Persisted immutable PVC VAT history is inconsistent."""


@dataclass(frozen=True, slots=True)
class PurchaseValueCorrectionVatAdjustmentSourcePlan:
    reversal_event_ids: tuple[int, ...]
    replacement_target: PurchaseValueCorrectionVatAdjustmentTarget | None

    @property
    def is_noop(self) -> bool:
        return (
            not self.reversal_event_ids
            and self.replacement_target is None
        )


@dataclass(frozen=True, slots=True)
class PurchaseValueCorrectionVatAdjustmentReconciliationResult:
    created_events: tuple[
        PurchaseValueCorrectionVatAdjustmentEvent,
        ...,
    ]

    @property
    def is_noop(self) -> bool:
        return not self.created_events


def _positive_id(value, *, field: str) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value <= 0
    ):
        raise PurchaseValueCorrectionVatAdjustmentPersistenceStateError(
            f"{field} must be a positive integer"
        )
    return value


def _amount(value, *, field: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except Exception as exc:
        raise PurchaseValueCorrectionVatAdjustmentPersistenceIntegrityError(
            f"{field} must be Decimal-compatible"
        ) from exc

    if not result.is_finite():
        raise PurchaseValueCorrectionVatAdjustmentPersistenceIntegrityError(
            f"{field} must be finite"
        )

    if result < ZERO:
        raise PurchaseValueCorrectionVatAdjustmentPersistenceIntegrityError(
            f"{field} cannot be negative"
        )

    return result


def _validate_target(
    target: PurchaseValueCorrectionVatAdjustmentTarget,
) -> None:
    if not isinstance(
        target,
        PurchaseValueCorrectionVatAdjustmentTarget,
    ):
        raise PurchaseValueCorrectionVatAdjustmentPersistenceStateError(
            "target has invalid type"
        )

    _positive_id(
        target.trade_value_correction_event_id,
        field="trade_value_correction_event_id",
    )
    _positive_id(
        target.tax_calculation_id,
        field="tax_calculation_id",
    )

    if not isinstance(target.adjustment_date, date):
        raise PurchaseValueCorrectionVatAdjustmentPersistenceStateError(
            "target adjustment_date must be a date"
        )

    if target.adjustment_kind not in {"decrease", "increase"}:
        raise PurchaseValueCorrectionVatAdjustmentPersistenceStateError(
            "target adjustment_kind must be decrease or increase"
        )

    base = _amount(
        target.adjusted_taxable_base,
        field="target adjusted_taxable_base",
    )
    tax = _amount(
        target.adjusted_tax_amount,
        field="target adjusted_tax_amount",
    )

    if base == ZERO and tax == ZERO:
        return

    if (
        not isinstance(target.currency_code, str)
        or len(target.currency_code) != 3
    ):
        raise PurchaseValueCorrectionVatAdjustmentPersistenceStateError(
            "target currency_code must contain exactly 3 characters"
        )


def _event_id(
    event: PurchaseValueCorrectionVatAdjustmentEvent,
) -> int:
    if (
        event.id is None
        or not isinstance(event.id, int)
        or event.id <= 0
    ):
        raise PurchaseValueCorrectionVatAdjustmentPersistenceIntegrityError(
            "Persisted PVC VAT event must have a positive id"
        )
    return event.id


def _validate_history_event(
    *,
    company_id: int,
    target: PurchaseValueCorrectionVatAdjustmentTarget,
    event: PurchaseValueCorrectionVatAdjustmentEvent,
) -> None:
    _event_id(event)

    if event.company_id != company_id:
        raise PurchaseValueCorrectionVatAdjustmentPersistenceIntegrityError(
            "PVC VAT event company mismatch"
        )

    if (
        event.trade_value_correction_event_id
        != target.trade_value_correction_event_id
        or event.tax_calculation_id != target.tax_calculation_id
        or event.adjustment_kind != target.adjustment_kind
    ):
        raise PurchaseValueCorrectionVatAdjustmentPersistenceIntegrityError(
            "PVC VAT history contains a different source identity"
        )

    if event.currency_code != target.currency_code:
        raise PurchaseValueCorrectionVatAdjustmentPersistenceIntegrityError(
            "PVC VAT history currency mismatch"
        )

    base = _amount(
        event.adjusted_taxable_base,
        field="event adjusted_taxable_base",
    )
    tax = _amount(
        event.adjusted_tax_amount,
        field="event adjusted_tax_amount",
    )

    if base == ZERO and tax == ZERO:
        raise PurchaseValueCorrectionVatAdjustmentPersistenceIntegrityError(
            "Persisted PVC VAT event cannot be zero-zero"
        )

    if not isinstance(event.adjustment_date, date):
        raise PurchaseValueCorrectionVatAdjustmentPersistenceIntegrityError(
            "Persisted PVC VAT adjustment_date must be a date"
        )


def _active_originals(
    *,
    company_id: int,
    target: PurchaseValueCorrectionVatAdjustmentTarget,
    events: Iterable[PurchaseValueCorrectionVatAdjustmentEvent],
) -> tuple[PurchaseValueCorrectionVatAdjustmentEvent, ...]:
    rows = tuple(events)
    by_id: dict[int, PurchaseValueCorrectionVatAdjustmentEvent] = {}

    for event in rows:
        _validate_history_event(
            company_id=company_id,
            target=target,
            event=event,
        )
        event_id = _event_id(event)

        if event_id in by_id:
            raise PurchaseValueCorrectionVatAdjustmentPersistenceIntegrityError(
                "Duplicate PVC VAT event id"
            )

        by_id[event_id] = event

    reversed_ids: set[int] = set()

    for event in rows:
        if event.reversal_of_id is None:
            continue

        original = by_id.get(event.reversal_of_id)

        if original is None:
            raise PurchaseValueCorrectionVatAdjustmentPersistenceIntegrityError(
                "PVC VAT reversal references missing original"
            )

        if original.reversal_of_id is not None:
            raise PurchaseValueCorrectionVatAdjustmentPersistenceIntegrityError(
                "PVC VAT reversal-of-reversal is not allowed"
            )

        if original.id in reversed_ids:
            raise PurchaseValueCorrectionVatAdjustmentPersistenceIntegrityError(
                "PVC VAT original has multiple reversals"
            )

        if (
            _amount(
                event.adjusted_taxable_base,
                field="reversal adjusted_taxable_base",
            )
            != _amount(
                original.adjusted_taxable_base,
                field="original adjusted_taxable_base",
            )
            or _amount(
                event.adjusted_tax_amount,
                field="reversal adjusted_tax_amount",
            )
            != _amount(
                original.adjusted_tax_amount,
                field="original adjusted_tax_amount",
            )
            or event.currency_code != original.currency_code
        ):
            raise PurchaseValueCorrectionVatAdjustmentPersistenceIntegrityError(
                "PVC VAT reversal must preserve original amounts/currency"
            )

        reversed_ids.add(original.id)

    active = tuple(
        event
        for event in rows
        if (
            event.reversal_of_id is None
            and _event_id(event) not in reversed_ids
        )
    )

    if len(active) > 1:
        raise PurchaseValueCorrectionVatAdjustmentPersistenceIntegrityError(
            "PVC VAT source has multiple active originals"
        )

    return active


def _same_state(
    *,
    event: PurchaseValueCorrectionVatAdjustmentEvent,
    target: PurchaseValueCorrectionVatAdjustmentTarget,
) -> bool:
    return (
        event.adjustment_date == target.adjustment_date
        and _amount(
            event.adjusted_taxable_base,
            field="event adjusted_taxable_base",
        )
        == _amount(
            target.adjusted_taxable_base,
            field="target adjusted_taxable_base",
        )
        and _amount(
            event.adjusted_tax_amount,
            field="event adjusted_tax_amount",
        )
        == _amount(
            target.adjusted_tax_amount,
            field="target adjusted_tax_amount",
        )
        and event.currency_code == target.currency_code
    )


def build_purchase_value_correction_vat_adjustment_source_plan(
    *,
    company_id: int,
    target: PurchaseValueCorrectionVatAdjustmentTarget,
    events: Iterable[PurchaseValueCorrectionVatAdjustmentEvent],
) -> PurchaseValueCorrectionVatAdjustmentSourcePlan:
    company_id = _positive_id(company_id, field="company_id")
    _validate_target(target)

    active = _active_originals(
        company_id=company_id,
        target=target,
        events=events,
    )

    base = _amount(
        target.adjusted_taxable_base,
        field="target adjusted_taxable_base",
    )
    tax = _amount(
        target.adjusted_tax_amount,
        field="target adjusted_tax_amount",
    )

    desired_zero = base == ZERO and tax == ZERO

    if not active:
        return PurchaseValueCorrectionVatAdjustmentSourcePlan(
            reversal_event_ids=(),
            replacement_target=None if desired_zero else target,
        )

    current = active[0]
    current_id = _event_id(current)

    if desired_zero:
        return PurchaseValueCorrectionVatAdjustmentSourcePlan(
            reversal_event_ids=(current_id,),
            replacement_target=None,
        )

    if _same_state(event=current, target=target):
        return PurchaseValueCorrectionVatAdjustmentSourcePlan(
            reversal_event_ids=(),
            replacement_target=None,
        )

    return PurchaseValueCorrectionVatAdjustmentSourcePlan(
        reversal_event_ids=(current_id,),
        replacement_target=target,
    )


async def reconcile_purchase_value_correction_vat_adjustment(
    *,
    db: AsyncSession,
    company_id: int,
    target: PurchaseValueCorrectionVatAdjustmentTarget,
    created_by: int,
    adjustment_date: date | None = None,
) -> PurchaseValueCorrectionVatAdjustmentReconciliationResult:
    company_id = _positive_id(company_id, field="company_id")
    created_by = _positive_id(created_by, field="created_by")
    _validate_target(target)

    if adjustment_date is not None and adjustment_date < target.adjustment_date:
        raise PurchaseValueCorrectionVatAdjustmentPersistenceStateError(
            "adjustment_date cannot precede target adjustment_date"
        )

    history_result = await db.execute(
        select(PurchaseValueCorrectionVatAdjustmentEvent)
        .where(
            PurchaseValueCorrectionVatAdjustmentEvent.company_id
            == company_id,
            PurchaseValueCorrectionVatAdjustmentEvent
            .trade_value_correction_event_id
            == target.trade_value_correction_event_id,
            PurchaseValueCorrectionVatAdjustmentEvent.tax_calculation_id
            == target.tax_calculation_id,
            PurchaseValueCorrectionVatAdjustmentEvent.adjustment_kind
            == target.adjustment_kind,
        )
        .order_by(PurchaseValueCorrectionVatAdjustmentEvent.id)
        .with_for_update()
    )

    history = tuple(history_result.scalars().all())

    plan = build_purchase_value_correction_vat_adjustment_source_plan(
        company_id=company_id,
        target=target,
        events=history,
    )

    if plan.is_noop:
        return PurchaseValueCorrectionVatAdjustmentReconciliationResult(
            created_events=()
        )

    by_id = {
        _event_id(event): event
        for event in history
    }

    created: list[PurchaseValueCorrectionVatAdjustmentEvent] = []
    write_date = adjustment_date or target.adjustment_date

    for original_id in plan.reversal_event_ids:
        original = by_id.get(original_id)

        if original is None:
            raise PurchaseValueCorrectionVatAdjustmentPersistenceIntegrityError(
                "Planned PVC VAT reversal original is missing"
            )

        reversal = PurchaseValueCorrectionVatAdjustmentEvent(
            company_id=company_id,
            trade_value_correction_event_id=(
                original.trade_value_correction_event_id
            ),
            tax_calculation_id=original.tax_calculation_id,
            adjustment_date=write_date,
            adjustment_kind=original.adjustment_kind,
            adjusted_taxable_base=original.adjusted_taxable_base,
            adjusted_tax_amount=original.adjusted_tax_amount,
            currency_code=original.currency_code,
            created_by=created_by,
            reversal_of_id=original.id,
        )

        db.add(reversal)
        created.append(reversal)

    if plan.replacement_target is not None:
        replacement = plan.replacement_target

        replacement_event = PurchaseValueCorrectionVatAdjustmentEvent(
            company_id=company_id,
            trade_value_correction_event_id=(
                replacement.trade_value_correction_event_id
            ),
            tax_calculation_id=replacement.tax_calculation_id,
            adjustment_date=max(
                replacement.adjustment_date,
                write_date,
            ),
            adjustment_kind=replacement.adjustment_kind,
            adjusted_taxable_base=replacement.adjusted_taxable_base,
            adjusted_tax_amount=replacement.adjusted_tax_amount,
            currency_code=replacement.currency_code,
            created_by=created_by,
            reversal_of_id=None,
        )

        db.add(replacement_event)
        created.append(replacement_event)

    await db.flush()

    return PurchaseValueCorrectionVatAdjustmentReconciliationResult(
        created_events=tuple(created)
    )
