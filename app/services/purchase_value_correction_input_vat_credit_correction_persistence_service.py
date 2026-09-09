from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.purchase_value_correction_input_vat_credit_correction_event import (
    PurchaseValueCorrectionInputVatCreditCorrectionEvent,
)
from app.services.purchase_value_correction_input_vat_credit_correction_calculation_service import (
    PurchaseValueCorrectionInputVatCreditCorrectionTarget,
)


ZERO = Decimal("0")


class PurchaseValueCorrectionInputVatCreditCorrectionPersistenceError(
    Exception
):
    """Base immutable PVC legal INPUT VAT persistence error."""


class PurchaseValueCorrectionInputVatCreditCorrectionPersistenceStateError(
    PurchaseValueCorrectionInputVatCreditCorrectionPersistenceError
):
    """Desired PVC legal-credit state is invalid."""


class PurchaseValueCorrectionInputVatCreditCorrectionPersistenceIntegrityError(
    PurchaseValueCorrectionInputVatCreditCorrectionPersistenceError
):
    """Persisted immutable PVC legal-credit history is inconsistent."""


@dataclass(frozen=True, slots=True)
class PurchaseValueCorrectionInputVatCreditCorrectionSourcePlan:
    reversal_event_ids: tuple[int, ...]
    replacement_target: (
        PurchaseValueCorrectionInputVatCreditCorrectionTarget | None
    )

    @property
    def is_noop(self) -> bool:
        return (
            not self.reversal_event_ids
            and self.replacement_target is None
        )


@dataclass(frozen=True, slots=True)
class PurchaseValueCorrectionInputVatCreditCorrectionReconciliationResult:
    created_events: tuple[
        PurchaseValueCorrectionInputVatCreditCorrectionEvent,
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
        raise (
            PurchaseValueCorrectionInputVatCreditCorrectionPersistenceStateError(
                f"{field} must be a positive integer"
            )
        )
    return value


def _amount(value, *, field: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except Exception as exc:
        raise (
            PurchaseValueCorrectionInputVatCreditCorrectionPersistenceIntegrityError(
                f"{field} must be Decimal-compatible"
            )
        ) from exc

    if not result.is_finite():
        raise (
            PurchaseValueCorrectionInputVatCreditCorrectionPersistenceIntegrityError(
                f"{field} must be finite"
            )
        )

    if result < ZERO:
        raise (
            PurchaseValueCorrectionInputVatCreditCorrectionPersistenceIntegrityError(
                f"{field} cannot be negative"
            )
        )

    return result


def _validate_target(
    target: PurchaseValueCorrectionInputVatCreditCorrectionTarget,
) -> None:
    if not isinstance(
        target,
        PurchaseValueCorrectionInputVatCreditCorrectionTarget,
    ):
        raise (
            PurchaseValueCorrectionInputVatCreditCorrectionPersistenceStateError(
                "target has invalid type"
            )
        )

    _positive_id(
        target.purchase_value_correction_vat_adjustment_event_id,
        field="purchase_value_correction_vat_adjustment_event_id",
    )
    _positive_id(
        target.tax_calculation_id,
        field="tax_calculation_id",
    )

    if not isinstance(target.adjustment_date, date):
        raise (
            PurchaseValueCorrectionInputVatCreditCorrectionPersistenceStateError(
                "target adjustment_date must be a date"
            )
        )

    if target.correction_kind not in {"decrease", "increase"}:
        raise (
            PurchaseValueCorrectionInputVatCreditCorrectionPersistenceStateError(
                "target correction_kind must be decrease or increase"
            )
        )

    base = _amount(
        target.corrected_taxable_base,
        field="target corrected_taxable_base",
    )
    tax = _amount(
        target.corrected_tax_amount,
        field="target corrected_tax_amount",
    )

    if (
        target.correction_kind == "increase"
        and target.tax_credit_evidence_id is None
    ):
        raise (
            PurchaseValueCorrectionInputVatCreditCorrectionPersistenceStateError(
                "increase requires tax_credit_evidence_id"
            )
        )

    if (
        target.correction_kind == "decrease"
        and target.tax_credit_evidence_id is not None
    ):
        raise (
            PurchaseValueCorrectionInputVatCreditCorrectionPersistenceStateError(
                "decrease must not carry tax_credit_evidence_id"
            )
        )

    if target.tax_credit_evidence_id is not None:
        _positive_id(
            target.tax_credit_evidence_id,
            field="tax_credit_evidence_id",
        )

    if base == ZERO and tax == ZERO:
        return

    if (
        not isinstance(target.currency_code, str)
        or len(target.currency_code) != 3
    ):
        raise (
            PurchaseValueCorrectionInputVatCreditCorrectionPersistenceStateError(
                "target currency_code must contain exactly 3 characters"
            )
        )


def _event_id(
    event: PurchaseValueCorrectionInputVatCreditCorrectionEvent,
) -> int:
    if (
        event.id is None
        or not isinstance(event.id, int)
        or event.id <= 0
    ):
        raise (
            PurchaseValueCorrectionInputVatCreditCorrectionPersistenceIntegrityError(
                "Persisted PVC legal-credit event must have a positive id"
            )
        )
    return event.id


def _validate_history_event(
    *,
    company_id: int,
    target: PurchaseValueCorrectionInputVatCreditCorrectionTarget,
    event: PurchaseValueCorrectionInputVatCreditCorrectionEvent,
) -> None:
    _event_id(event)

    if event.company_id != company_id:
        raise (
            PurchaseValueCorrectionInputVatCreditCorrectionPersistenceIntegrityError(
                "PVC legal-credit event company mismatch"
            )
        )

    if (
        event.purchase_value_correction_vat_adjustment_event_id
        != target.purchase_value_correction_vat_adjustment_event_id
        or event.tax_calculation_id != target.tax_calculation_id
        or event.correction_kind != target.correction_kind
    ):
        raise (
            PurchaseValueCorrectionInputVatCreditCorrectionPersistenceIntegrityError(
                "PVC legal-credit history contains different source identity"
            )
        )

    if event.currency_code != target.currency_code:
        raise (
            PurchaseValueCorrectionInputVatCreditCorrectionPersistenceIntegrityError(
                "PVC legal-credit history currency mismatch"
            )
        )

    base = _amount(
        event.corrected_taxable_base,
        field="event corrected_taxable_base",
    )
    tax = _amount(
        event.corrected_tax_amount,
        field="event corrected_tax_amount",
    )

    if base == ZERO and tax == ZERO:
        raise (
            PurchaseValueCorrectionInputVatCreditCorrectionPersistenceIntegrityError(
                "Persisted PVC legal-credit event cannot be zero-zero"
            )
        )

    if not isinstance(event.adjustment_date, date):
        raise (
            PurchaseValueCorrectionInputVatCreditCorrectionPersistenceIntegrityError(
                "Persisted PVC legal-credit adjustment_date must be a date"
            )
        )

    if event.correction_kind == "increase":
        if event.tax_credit_evidence_id is None:
            raise (
                PurchaseValueCorrectionInputVatCreditCorrectionPersistenceIntegrityError(
                    "Persisted increase requires tax-credit evidence"
                )
            )
    elif event.tax_credit_evidence_id is not None:
        raise (
            PurchaseValueCorrectionInputVatCreditCorrectionPersistenceIntegrityError(
                "Persisted decrease cannot carry tax-credit evidence"
            )
        )


def _active_originals(
    *,
    company_id: int,
    target: PurchaseValueCorrectionInputVatCreditCorrectionTarget,
    events: Iterable[
        PurchaseValueCorrectionInputVatCreditCorrectionEvent
    ],
) -> tuple[
    PurchaseValueCorrectionInputVatCreditCorrectionEvent,
    ...,
]:
    rows = tuple(events)
    by_id: dict[
        int,
        PurchaseValueCorrectionInputVatCreditCorrectionEvent,
    ] = {}

    for event in rows:
        _validate_history_event(
            company_id=company_id,
            target=target,
            event=event,
        )
        event_id = _event_id(event)

        if event_id in by_id:
            raise (
                PurchaseValueCorrectionInputVatCreditCorrectionPersistenceIntegrityError(
                    "Duplicate PVC legal-credit event id"
                )
            )

        by_id[event_id] = event

    reversed_ids: set[int] = set()

    for event in rows:
        if event.reversal_of_id is None:
            continue

        original = by_id.get(event.reversal_of_id)

        if original is None:
            raise (
                PurchaseValueCorrectionInputVatCreditCorrectionPersistenceIntegrityError(
                    "PVC legal-credit reversal references missing original"
                )
            )

        if original.reversal_of_id is not None:
            raise (
                PurchaseValueCorrectionInputVatCreditCorrectionPersistenceIntegrityError(
                    "PVC legal-credit reversal-of-reversal is not allowed"
                )
            )

        if original.id in reversed_ids:
            raise (
                PurchaseValueCorrectionInputVatCreditCorrectionPersistenceIntegrityError(
                    "PVC legal-credit original has multiple reversals"
                )
            )

        if (
            _amount(
                event.corrected_taxable_base,
                field="reversal corrected_taxable_base",
            )
            != _amount(
                original.corrected_taxable_base,
                field="original corrected_taxable_base",
            )
            or _amount(
                event.corrected_tax_amount,
                field="reversal corrected_tax_amount",
            )
            != _amount(
                original.corrected_tax_amount,
                field="original corrected_tax_amount",
            )
            or event.currency_code != original.currency_code
            or event.tax_credit_evidence_id
            != original.tax_credit_evidence_id
        ):
            raise (
                PurchaseValueCorrectionInputVatCreditCorrectionPersistenceIntegrityError(
                    "PVC legal-credit reversal must preserve original state"
                )
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
        raise (
            PurchaseValueCorrectionInputVatCreditCorrectionPersistenceIntegrityError(
                "PVC legal-credit source has multiple active originals"
            )
        )

    return active


def _same_state(
    *,
    event: PurchaseValueCorrectionInputVatCreditCorrectionEvent,
    target: PurchaseValueCorrectionInputVatCreditCorrectionTarget,
) -> bool:
    return (
        event.adjustment_date == target.adjustment_date
        and event.tax_credit_evidence_id == target.tax_credit_evidence_id
        and _amount(
            event.corrected_taxable_base,
            field="event corrected_taxable_base",
        )
        == _amount(
            target.corrected_taxable_base,
            field="target corrected_taxable_base",
        )
        and _amount(
            event.corrected_tax_amount,
            field="event corrected_tax_amount",
        )
        == _amount(
            target.corrected_tax_amount,
            field="target corrected_tax_amount",
        )
        and event.currency_code == target.currency_code
    )


def build_purchase_value_correction_input_vat_credit_correction_source_plan(
    *,
    company_id: int,
    target: PurchaseValueCorrectionInputVatCreditCorrectionTarget,
    events: Iterable[
        PurchaseValueCorrectionInputVatCreditCorrectionEvent
    ],
) -> PurchaseValueCorrectionInputVatCreditCorrectionSourcePlan:
    company_id = _positive_id(company_id, field="company_id")
    _validate_target(target)

    active = _active_originals(
        company_id=company_id,
        target=target,
        events=events,
    )

    base = _amount(
        target.corrected_taxable_base,
        field="target corrected_taxable_base",
    )
    tax = _amount(
        target.corrected_tax_amount,
        field="target corrected_tax_amount",
    )

    desired_zero = base == ZERO and tax == ZERO

    if not active:
        return PurchaseValueCorrectionInputVatCreditCorrectionSourcePlan(
            reversal_event_ids=(),
            replacement_target=None if desired_zero else target,
        )

    current = active[0]
    current_id = _event_id(current)

    if desired_zero:
        return PurchaseValueCorrectionInputVatCreditCorrectionSourcePlan(
            reversal_event_ids=(current_id,),
            replacement_target=None,
        )

    if _same_state(event=current, target=target):
        return PurchaseValueCorrectionInputVatCreditCorrectionSourcePlan(
            reversal_event_ids=(),
            replacement_target=None,
        )

    return PurchaseValueCorrectionInputVatCreditCorrectionSourcePlan(
        reversal_event_ids=(current_id,),
        replacement_target=target,
    )


async def reconcile_purchase_value_correction_input_vat_credit_correction(
    *,
    db: AsyncSession,
    company_id: int,
    target: PurchaseValueCorrectionInputVatCreditCorrectionTarget,
    created_by: int,
    adjustment_date: date | None = None,
) -> PurchaseValueCorrectionInputVatCreditCorrectionReconciliationResult:
    company_id = _positive_id(company_id, field="company_id")
    created_by = _positive_id(created_by, field="created_by")
    _validate_target(target)

    if adjustment_date is not None and adjustment_date < target.adjustment_date:
        raise (
            PurchaseValueCorrectionInputVatCreditCorrectionPersistenceStateError(
                "adjustment_date cannot precede target adjustment_date"
            )
        )

    history_result = await db.execute(
        select(PurchaseValueCorrectionInputVatCreditCorrectionEvent)
        .where(
            PurchaseValueCorrectionInputVatCreditCorrectionEvent.company_id
            == company_id,
            PurchaseValueCorrectionInputVatCreditCorrectionEvent
            .purchase_value_correction_vat_adjustment_event_id
            == target.purchase_value_correction_vat_adjustment_event_id,
            PurchaseValueCorrectionInputVatCreditCorrectionEvent
            .tax_calculation_id
            == target.tax_calculation_id,
            PurchaseValueCorrectionInputVatCreditCorrectionEvent
            .correction_kind
            == target.correction_kind,
        )
        .order_by(
            PurchaseValueCorrectionInputVatCreditCorrectionEvent.id
        )
        .with_for_update()
    )

    history = tuple(history_result.scalars().all())

    plan = (
        build_purchase_value_correction_input_vat_credit_correction_source_plan(
            company_id=company_id,
            target=target,
            events=history,
        )
    )

    if plan.is_noop:
        return (
            PurchaseValueCorrectionInputVatCreditCorrectionReconciliationResult(
                created_events=()
            )
        )

    by_id = {
        _event_id(event): event
        for event in history
    }

    created: list[
        PurchaseValueCorrectionInputVatCreditCorrectionEvent
    ] = []

    write_date = adjustment_date or target.adjustment_date

    for original_id in plan.reversal_event_ids:
        original = by_id.get(original_id)

        if original is None:
            raise (
                PurchaseValueCorrectionInputVatCreditCorrectionPersistenceIntegrityError(
                    "Planned PVC legal-credit reversal original is missing"
                )
            )

        reversal = (
            PurchaseValueCorrectionInputVatCreditCorrectionEvent(
                company_id=company_id,
                purchase_value_correction_vat_adjustment_event_id=(
                    original
                    .purchase_value_correction_vat_adjustment_event_id
                ),
                tax_calculation_id=original.tax_calculation_id,
                tax_credit_evidence_id=original.tax_credit_evidence_id,
                adjustment_date=write_date,
                correction_kind=original.correction_kind,
                corrected_taxable_base=original.corrected_taxable_base,
                corrected_tax_amount=original.corrected_tax_amount,
                currency_code=original.currency_code,
                created_by=created_by,
                reversal_of_id=original.id,
            )
        )

        db.add(reversal)
        created.append(reversal)

    if plan.replacement_target is not None:
        replacement = plan.replacement_target

        replacement_event = (
            PurchaseValueCorrectionInputVatCreditCorrectionEvent(
                company_id=company_id,
                purchase_value_correction_vat_adjustment_event_id=(
                    replacement
                    .purchase_value_correction_vat_adjustment_event_id
                ),
                tax_calculation_id=replacement.tax_calculation_id,
                tax_credit_evidence_id=(
                    replacement.tax_credit_evidence_id
                ),
                adjustment_date=max(
                    replacement.adjustment_date,
                    write_date,
                ),
                correction_kind=replacement.correction_kind,
                corrected_taxable_base=(
                    replacement.corrected_taxable_base
                ),
                corrected_tax_amount=(
                    replacement.corrected_tax_amount
                ),
                currency_code=replacement.currency_code,
                created_by=created_by,
                reversal_of_id=None,
            )
        )

        db.add(replacement_event)
        created.append(replacement_event)

    await db.flush()

    return (
        PurchaseValueCorrectionInputVatCreditCorrectionReconciliationResult(
            created_events=tuple(created)
        )
    )
