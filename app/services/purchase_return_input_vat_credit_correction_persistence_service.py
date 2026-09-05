from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.purchase_return_input_vat_credit_correction_event import (
    PurchaseReturnInputVatCreditCorrectionEvent,
)
from app.services.purchase_return_input_vat_credit_correction_calculation_service import (
    PurchaseReturnInputVatCreditCorrectionTarget,
)


ZERO = Decimal("0")


class PurchaseReturnInputVatCreditCorrectionPersistenceError(
    Exception
):
    """Base immutable legal-credit persistence error."""


class PurchaseReturnInputVatCreditCorrectionPersistenceStateError(
    PurchaseReturnInputVatCreditCorrectionPersistenceError
):
    """Desired/current correction state is invalid."""


class PurchaseReturnInputVatCreditCorrectionPersistenceIntegrityError(
    PurchaseReturnInputVatCreditCorrectionPersistenceError
):
    """Persisted immutable correction history is inconsistent."""


@dataclass(
    frozen=True,
    slots=True,
)
class PurchaseReturnInputVatCreditCorrectionSourcePlan:
    reversal_event_ids: tuple[
        int,
        ...,
    ]
    replacement_target: (
        PurchaseReturnInputVatCreditCorrectionTarget
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
    value,
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
            PurchaseReturnInputVatCreditCorrectionPersistenceStateError(
                f"{field} must be a positive integer"
            )
        )

    return value


def _amount(
    value,
    *,
    field: str,
) -> Decimal:
    try:
        result = Decimal(
            str(
                value
            )
        )
    except Exception as exc:
        raise (
            PurchaseReturnInputVatCreditCorrectionPersistenceIntegrityError(
                f"{field} must be Decimal-compatible"
            )
        ) from exc

    if not result.is_finite():
        raise (
            PurchaseReturnInputVatCreditCorrectionPersistenceIntegrityError(
                f"{field} must be finite"
            )
        )

    if result < ZERO:
        raise (
            PurchaseReturnInputVatCreditCorrectionPersistenceIntegrityError(
                f"{field} cannot be negative"
            )
        )

    return result


def _validate_target(
    target: PurchaseReturnInputVatCreditCorrectionTarget,
) -> None:
    if not isinstance(
        target,
        PurchaseReturnInputVatCreditCorrectionTarget,
    ):
        raise (
            PurchaseReturnInputVatCreditCorrectionPersistenceStateError(
                "target has invalid type"
            )
        )

    _positive_id(
        target.purchase_return_vat_adjustment_event_id,
        field=(
            "purchase_return_vat_adjustment_event_id"
        ),
    )

    _positive_id(
        target.tax_calculation_id,
        field="tax_calculation_id",
    )

    if not isinstance(
        target.adjustment_date,
        date,
    ):
        raise (
            PurchaseReturnInputVatCreditCorrectionPersistenceStateError(
                "target adjustment_date must be a date"
            )
        )

    _amount(
        target.reduced_taxable_base,
        field="target reduced_taxable_base",
    )

    _amount(
        target.reduced_tax_amount,
        field="target reduced_tax_amount",
    )

    if (
        not isinstance(
            target.currency_code,
            str,
        )
        or len(
            target.currency_code
        ) != 3
    ):
        raise (
            PurchaseReturnInputVatCreditCorrectionPersistenceStateError(
                "target currency_code must contain "
                "exactly 3 characters"
            )
        )


def _history_rows(
    events: Iterable[
        PurchaseReturnInputVatCreditCorrectionEvent
    ],
) -> tuple[
    PurchaseReturnInputVatCreditCorrectionEvent,
    ...,
]:
    return tuple(
        events
    )


def build_purchase_return_input_vat_credit_correction_source_plan(
    *,
    company_id: int,
    target: PurchaseReturnInputVatCreditCorrectionTarget,
    events: Iterable[
        PurchaseReturnInputVatCreditCorrectionEvent
    ],
) -> PurchaseReturnInputVatCreditCorrectionSourcePlan:
    """
    Reconcile one immutable source identity:

        PurchaseReturnVatAdjustmentEvent
        + TaxCalculation

    adjustment_date is desired state, not source identity.

    Positive desired:
        no active original -> create original
        exact active       -> noop
        changed active     -> reversal + replacement

    Zero desired:
        active original    -> reversal only
        no active original -> noop

    A fully reversed historical source may later receive a new
    positive original.

    No UPDATE or DELETE occurs.
    """

    company_id = _positive_id(
        company_id,
        field="company_id",
    )

    _validate_target(
        target
    )

    rows = _history_rows(
        events
    )

    by_id = {}

    originals = []
    reversals = []

    for event in rows:
        if (
            event.id is None
            or event.id <= 0
        ):
            raise (
                PurchaseReturnInputVatCreditCorrectionPersistenceIntegrityError(
                    "Persisted correction event must have "
                    "a positive ID"
                )
            )

        if event.id in by_id:
            raise (
                PurchaseReturnInputVatCreditCorrectionPersistenceIntegrityError(
                    "Duplicate correction event ID"
                )
            )

        by_id[
            event.id
        ] = event

        if event.company_id != company_id:
            raise (
                PurchaseReturnInputVatCreditCorrectionPersistenceIntegrityError(
                    "Correction event company mismatch"
                )
            )

        if (
            event.purchase_return_vat_adjustment_event_id
            != (
                target
                .purchase_return_vat_adjustment_event_id
            )
        ):
            raise (
                PurchaseReturnInputVatCreditCorrectionPersistenceIntegrityError(
                    "Correction event Purchase Return VAT "
                    "source mismatch"
                )
            )

        if (
            event.tax_calculation_id
            != target.tax_calculation_id
        ):
            raise (
                PurchaseReturnInputVatCreditCorrectionPersistenceIntegrityError(
                    "Correction event TaxCalculation mismatch"
                )
            )

        if (
            event.currency_code
            != target.currency_code
        ):
            raise (
                PurchaseReturnInputVatCreditCorrectionPersistenceIntegrityError(
                    "Correction event currency mismatch"
                )
            )

        _amount(
            event.reduced_taxable_base,
            field=(
                "event reduced_taxable_base"
            ),
        )

        _amount(
            event.reduced_tax_amount,
            field="event reduced_tax_amount",
        )

        if event.reversal_of_id is None:
            originals.append(
                event
            )

        else:
            reversals.append(
                event
            )

    reversed_ids = set()

    for reversal in reversals:
        original = by_id.get(
            reversal.reversal_of_id
        )

        if original is None:
            raise (
                PurchaseReturnInputVatCreditCorrectionPersistenceIntegrityError(
                    "Correction reversal references "
                    "missing original"
                )
            )

        if original.reversal_of_id is not None:
            raise (
                PurchaseReturnInputVatCreditCorrectionPersistenceIntegrityError(
                    "Correction reversal-of-reversal "
                    "is not allowed"
                )
            )

        if original.id in reversed_ids:
            raise (
                PurchaseReturnInputVatCreditCorrectionPersistenceIntegrityError(
                    "Multiple reversals of one "
                    "correction original"
                )
            )

        reversed_ids.add(
            original.id
        )

        if (
            _amount(
                reversal.reduced_taxable_base,
                field=(
                    "reversal reduced_taxable_base"
                ),
            )
            != _amount(
                original.reduced_taxable_base,
                field=(
                    "original reduced_taxable_base"
                ),
            )
        ):
            raise (
                PurchaseReturnInputVatCreditCorrectionPersistenceIntegrityError(
                    "Correction reversal must copy "
                    "historical taxable base"
                )
            )

        if (
            _amount(
                reversal.reduced_tax_amount,
                field="reversal reduced_tax_amount",
            )
            != _amount(
                original.reduced_tax_amount,
                field="original reduced_tax_amount",
            )
        ):
            raise (
                PurchaseReturnInputVatCreditCorrectionPersistenceIntegrityError(
                    "Correction reversal must copy "
                    "historical tax amount"
                )
            )

        if (
            reversal.adjustment_date
            < original.adjustment_date
        ):
            raise (
                PurchaseReturnInputVatCreditCorrectionPersistenceIntegrityError(
                    "Correction reversal date "
                    "precedes original"
                )
            )

    active_originals = [
        event
        for event in originals
        if event.id not in reversed_ids
    ]

    if len(
        active_originals
    ) > 1:
        raise (
            PurchaseReturnInputVatCreditCorrectionPersistenceIntegrityError(
                "Multiple ACTIVE correction originals "
                "for one source"
            )
        )

    if not active_originals:
        if target.is_zero:
            return (
                PurchaseReturnInputVatCreditCorrectionSourcePlan(
                    reversal_event_ids=(),
                    replacement_target=None,
                )
            )

        return (
            PurchaseReturnInputVatCreditCorrectionSourcePlan(
                reversal_event_ids=(),
                replacement_target=target,
            )
        )

    current = active_originals[
        0
    ]

    current_base = _amount(
        current.reduced_taxable_base,
        field="current reduced_taxable_base",
    )

    current_tax = _amount(
        current.reduced_tax_amount,
        field="current reduced_tax_amount",
    )

    target_base = _amount(
        target.reduced_taxable_base,
        field="target reduced_taxable_base",
    )

    target_tax = _amount(
        target.reduced_tax_amount,
        field="target reduced_tax_amount",
    )

    if (
        current_base == target_base
        and current_tax == target_tax
        and current.adjustment_date
        == target.adjustment_date
    ):
        return (
            PurchaseReturnInputVatCreditCorrectionSourcePlan(
                reversal_event_ids=(),
                replacement_target=None,
            )
        )

    if target.is_zero:
        return (
            PurchaseReturnInputVatCreditCorrectionSourcePlan(
                reversal_event_ids=(
                    current.id,
                ),
                replacement_target=None,
            )
        )

    return (
        PurchaseReturnInputVatCreditCorrectionSourcePlan(
            reversal_event_ids=(
                current.id,
            ),
            replacement_target=target,
        )
    )


async def _load_locked_source_history(
    db: AsyncSession,
    *,
    company_id: int,
    target: PurchaseReturnInputVatCreditCorrectionTarget,
) -> tuple[
    PurchaseReturnInputVatCreditCorrectionEvent,
    ...,
]:
    return tuple(
        (
            await db.execute(
                select(
                    PurchaseReturnInputVatCreditCorrectionEvent
                )
                .where(
                    (
                        PurchaseReturnInputVatCreditCorrectionEvent
                        .company_id
                        == company_id
                    ),
                    (
                        PurchaseReturnInputVatCreditCorrectionEvent
                        .purchase_return_vat_adjustment_event_id
                        == (
                            target
                            .purchase_return_vat_adjustment_event_id
                        )
                    ),
                    (
                        PurchaseReturnInputVatCreditCorrectionEvent
                        .tax_calculation_id
                        == target.tax_calculation_id
                    ),
                )
                .order_by(
                    PurchaseReturnInputVatCreditCorrectionEvent.id
                )
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )


async def reconcile_purchase_return_input_vat_credit_correction_source(
    db: AsyncSession,
    *,
    company_id: int,
    target: PurchaseReturnInputVatCreditCorrectionTarget,
    created_by: int,
    reversal_date: date | None = None,
) -> tuple[
    PurchaseReturnInputVatCreditCorrectionEvent,
    ...,
]:
    """
    Persist one immutable source reconciliation.

    Caller owns transaction boundaries.
    """

    company_id = _positive_id(
        company_id,
        field="company_id",
    )

    created_by = _positive_id(
        created_by,
        field="created_by",
    )

    _validate_target(
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
            PurchaseReturnInputVatCreditCorrectionPersistenceStateError(
                "reversal_date must be a date"
            )
        )

    history = (
        await _load_locked_source_history(
            db,
            company_id=company_id,
            target=target,
        )
    )

    plan = (
        build_purchase_return_input_vat_credit_correction_source_plan(
            company_id=company_id,
            target=target,
            events=history,
        )
    )

    if plan.is_noop:
        return ()

    by_id = {
        event.id:
        event
        for event in history
    }

    created = []

    for event_id in (
        plan.reversal_event_ids
    ):
        original = by_id.get(
            event_id
        )

        if original is None:
            raise (
                PurchaseReturnInputVatCreditCorrectionPersistenceIntegrityError(
                    "Correction original selected for "
                    "reversal does not exist"
                )
            )

        if (
            effective_reversal_date
            < original.adjustment_date
        ):
            raise (
                PurchaseReturnInputVatCreditCorrectionPersistenceStateError(
                    "reversal_date cannot precede "
                    "original adjustment_date"
                )
            )

        reversal = (
            PurchaseReturnInputVatCreditCorrectionEvent(
                company_id=company_id,
                purchase_return_vat_adjustment_event_id=(
                    original
                    .purchase_return_vat_adjustment_event_id
                ),
                tax_calculation_id=(
                    original.tax_calculation_id
                ),
                adjustment_date=(
                    effective_reversal_date
                ),
                reduced_taxable_base=(
                    original.reduced_taxable_base
                ),
                reduced_tax_amount=(
                    original.reduced_tax_amount
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
                PurchaseReturnInputVatCreditCorrectionPersistenceStateError(
                    "Zero correction target cannot be "
                    "persisted as an original"
                )
            )

        original = (
            PurchaseReturnInputVatCreditCorrectionEvent(
                company_id=company_id,
                purchase_return_vat_adjustment_event_id=(
                    replacement
                    .purchase_return_vat_adjustment_event_id
                ),
                tax_calculation_id=(
                    replacement.tax_calculation_id
                ),
                adjustment_date=(
                    replacement.adjustment_date
                ),
                reduced_taxable_base=(
                    replacement.reduced_taxable_base
                ),
                reduced_tax_amount=(
                    replacement.reduced_tax_amount
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
