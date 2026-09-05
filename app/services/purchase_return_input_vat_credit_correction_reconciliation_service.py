from dataclasses import (
    dataclass,
    replace,
)
from datetime import date
from decimal import Decimal
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.purchase_return_input_vat_credit_correction_event import (
    PurchaseReturnInputVatCreditCorrectionEvent,
)
from app.models.purchase_return_vat_adjustment_event import (
    PurchaseReturnVatAdjustmentEvent,
)
from app.models.tax_calculation import (
    TaxCalculation,
)
from app.models.tax_recognition_event import (
    TaxRecognitionEvent,
)
from app.services.input_tax_recognition_reconciliation_service import (
    InputTaxRecognitionReconciliationError,
    build_current_input_tax_recognition_targets,
)
from app.services.purchase_return_input_vat_credit_correction_calculation_service import (
    PurchaseReturnInputVatCreditCorrectionCalculationError,
    PurchaseReturnInputVatCreditCorrectionTarget,
    build_purchase_return_input_vat_credit_correction_target,
)
from app.services.purchase_return_input_vat_credit_correction_persistence_service import (
    PurchaseReturnInputVatCreditCorrectionPersistenceError,
    reconcile_purchase_return_input_vat_credit_correction_source,
)
from app.services.tax_types import (
    TaxDirection,
    TaxType,
)


ZERO = Decimal("0")


class PurchaseReturnInputVatCreditCorrectionReconciliationError(
    Exception
):
    """Base legal INPUT VAT return-correction reconciliation error."""


class PurchaseReturnInputVatCreditCorrectionReconciliationStateError(
    PurchaseReturnInputVatCreditCorrectionReconciliationError
):
    """Requested reconciliation state or chronology is invalid."""


class PurchaseReturnInputVatCreditCorrectionReconciliationIntegrityError(
    PurchaseReturnInputVatCreditCorrectionReconciliationError
):
    """Persisted immutable tax history is inconsistent."""


class PurchaseReturnInputVatCreditCorrectionReconciliationNotFoundError(
    PurchaseReturnInputVatCreditCorrectionReconciliationError
):
    """Requested TaxCalculation does not exist."""


@dataclass(
    frozen=True,
    slots=True,
)
class PurchaseReturnInputVatCreditCorrectionReconciliationResult:
    company_id: int
    tax_calculation_id: int
    adjustment_date: date
    calculation_taxable_base: Decimal
    calculation_tax_amount: Decimal
    formed_credit_taxable_base: Decimal
    formed_credit_tax_amount: Decimal
    active_return_event_ids: tuple[
        int,
        ...,
    ]
    current_correction_source_ids: tuple[
        int,
        ...,
    ]
    zeroed_correction_source_ids: tuple[
        int,
        ...,
    ]
    desired_targets: tuple[
        PurchaseReturnInputVatCreditCorrectionTarget,
        ...,
    ]
    created_events: tuple[
        PurchaseReturnInputVatCreditCorrectionEvent,
        ...,
    ]


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
            PurchaseReturnInputVatCreditCorrectionReconciliationStateError(
                f"{field} must be a positive integer"
            )
        )

    return value


def _business_date(
    value,
    *,
    field: str,
) -> date:
    if not isinstance(
        value,
        date,
    ):
        raise (
            PurchaseReturnInputVatCreditCorrectionReconciliationStateError(
                f"{field} must be a date"
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
            PurchaseReturnInputVatCreditCorrectionReconciliationIntegrityError(
                f"{field} must be Decimal-compatible"
            )
        ) from exc

    if not result.is_finite():
        raise (
            PurchaseReturnInputVatCreditCorrectionReconciliationIntegrityError(
                f"{field} must be finite"
            )
        )

    if result < ZERO:
        raise (
            PurchaseReturnInputVatCreditCorrectionReconciliationIntegrityError(
                f"{field} cannot be negative"
            )
        )

    return result


def _currency(
    value,
) -> str:
    result = str(
        value
    ).strip().upper()

    if len(
        result
    ) != 3:
        raise (
            PurchaseReturnInputVatCreditCorrectionReconciliationIntegrityError(
                "currency_code must contain exactly 3 characters"
            )
        )

    return result


def _active_original_rows(
    rows: Iterable,
    *,
    label: str,
    copied_fields: tuple[
        str,
        ...,
    ],
    date_field: str,
) -> tuple:
    """
    Resolve CURRENT active originals from the complete immutable
    history first.

    No business-date filtering occurs before reversal resolution.
    This is deliberate: filtering first can double-count a later
    replacement that retained an older business date.
    """

    history = tuple(
        rows
    )

    by_id = {}

    for row in history:
        row_id = getattr(
            row,
            "id",
            None,
        )

        if (
            not isinstance(
                row_id,
                int,
            )
            or isinstance(
                row_id,
                bool,
            )
            or row_id <= 0
        ):
            raise (
                PurchaseReturnInputVatCreditCorrectionReconciliationIntegrityError(
                    f"{label} must have a positive id"
                )
            )

        if row_id in by_id:
            raise (
                PurchaseReturnInputVatCreditCorrectionReconciliationIntegrityError(
                    f"Duplicate {label} id"
                )
            )

        by_id[
            row_id
        ] = row

    reversed_ids = set()

    for row in history:
        reversal_of_id = getattr(
            row,
            "reversal_of_id",
            None,
        )

        if reversal_of_id is None:
            continue

        if (
            not isinstance(
                reversal_of_id,
                int,
            )
            or isinstance(
                reversal_of_id,
                bool,
            )
            or reversal_of_id <= 0
        ):
            raise (
                PurchaseReturnInputVatCreditCorrectionReconciliationIntegrityError(
                    f"{label} reversal_of_id must be positive"
                )
            )

        original = by_id.get(
            reversal_of_id
        )

        if original is None:
            raise (
                PurchaseReturnInputVatCreditCorrectionReconciliationIntegrityError(
                    f"{label} reversal references missing original"
                )
            )

        if (
            getattr(
                original,
                "reversal_of_id",
                None,
            )
            is not None
        ):
            raise (
                PurchaseReturnInputVatCreditCorrectionReconciliationIntegrityError(
                    f"{label} reversal-of-reversal is not allowed"
                )
            )

        if reversal_of_id in reversed_ids:
            raise (
                PurchaseReturnInputVatCreditCorrectionReconciliationIntegrityError(
                    f"{label} original has multiple reversals"
                )
            )

        for field in copied_fields:
            if (
                getattr(
                    row,
                    field,
                    None,
                )
                != getattr(
                    original,
                    field,
                    None,
                )
            ):
                raise (
                    PurchaseReturnInputVatCreditCorrectionReconciliationIntegrityError(
                        f"{label} reversal must copy "
                        f"historical field {field}"
                    )
                )

        reversal_date = _business_date(
            getattr(
                row,
                date_field,
                None,
            ),
            field=(
                f"{label} reversal "
                f"{date_field}"
            ),
        )

        original_date = _business_date(
            getattr(
                original,
                date_field,
                None,
            ),
            field=(
                f"{label} original "
                f"{date_field}"
            ),
        )

        if reversal_date < original_date:
            raise (
                PurchaseReturnInputVatCreditCorrectionReconciliationIntegrityError(
                    f"{label} reversal date precedes original"
                )
            )

        reversed_ids.add(
            reversal_of_id
        )

    return tuple(
        row
        for row in history
        if (
            getattr(
                row,
                "reversal_of_id",
                None,
            )
            is None
            and row.id not in reversed_ids
        )
    )


def _validate_calculation(
    calculation,
    *,
    company_id: int,
    tax_calculation_id: int,
) -> tuple[
    Decimal,
    Decimal,
    str,
    date,
]:
    if calculation is None:
        raise (
            PurchaseReturnInputVatCreditCorrectionReconciliationNotFoundError(
                "TaxCalculation does not exist"
            )
        )

    if (
        calculation.id
        != tax_calculation_id
        or calculation.company_id
        != company_id
    ):
        raise (
            PurchaseReturnInputVatCreditCorrectionReconciliationIntegrityError(
                "TaxCalculation scope mismatch"
            )
        )

    try:
        tax_type = TaxType(
            calculation.tax_type
        )
    except ValueError as exc:
        raise (
            PurchaseReturnInputVatCreditCorrectionReconciliationIntegrityError(
                "Unsupported TaxCalculation tax_type"
            )
        ) from exc

    if tax_type != TaxType.VAT:
        raise (
            PurchaseReturnInputVatCreditCorrectionReconciliationStateError(
                "Purchase Return INPUT credit correction "
                "supports VAT only"
            )
        )

    try:
        direction = TaxDirection(
            calculation.direction
        )
    except ValueError as exc:
        raise (
            PurchaseReturnInputVatCreditCorrectionReconciliationIntegrityError(
                "Unsupported TaxCalculation direction"
            )
        ) from exc

    if direction != TaxDirection.INPUT:
        raise (
            PurchaseReturnInputVatCreditCorrectionReconciliationStateError(
                "Purchase Return credit correction "
                "requires INPUT TaxCalculation"
            )
        )

    calculation_base = _amount(
        calculation.taxable_base,
        field="TaxCalculation taxable_base",
    )

    calculation_tax = _amount(
        calculation.tax_amount,
        field="TaxCalculation tax_amount",
    )

    currency_code = _currency(
        calculation.currency_code
    )

    calculation_date = _business_date(
        calculation.calculation_date,
        field="TaxCalculation calculation_date",
    )

    return (
        calculation_base,
        calculation_tax,
        currency_code,
        calculation_date,
    )


def _validate_forward_chronology(
    *,
    adjustment_date: date,
    calculation_date: date,
    recognition_events: Iterable[
        TaxRecognitionEvent
    ],
    return_events: Iterable[
        PurchaseReturnVatAdjustmentEvent
    ],
    correction_events: Iterable[
        PurchaseReturnInputVatCreditCorrectionEvent
    ],
) -> None:
    """
    Normal automatic reconciliation is forward-only.

    This deliberately refuses historical replay after newer
    immutable business-dated events already exist.

    Historical migration/backfill requires a separate explicit
    replay mechanism rather than guessing with created_at.
    """

    dates = [
        calculation_date,
    ]

    for event in recognition_events:
        dates.append(
            _business_date(
                event.recognition_date,
                field=(
                    "TaxRecognitionEvent "
                    "recognition_date"
                ),
            )
        )

    for event in return_events:
        dates.append(
            _business_date(
                event.adjustment_date,
                field=(
                    "PurchaseReturnVatAdjustmentEvent "
                    "adjustment_date"
                ),
            )
        )

    for event in correction_events:
        dates.append(
            _business_date(
                event.adjustment_date,
                field=(
                    "PurchaseReturnInputVatCreditCorrectionEvent "
                    "adjustment_date"
                ),
            )
        )

    latest = max(
        dates
    )

    if adjustment_date < latest:
        raise (
            PurchaseReturnInputVatCreditCorrectionReconciliationStateError(
                "adjustment_date cannot precede the latest "
                "immutable business date for this "
                "TaxCalculation"
            )
        )


def _formed_input_credit(
    recognition_events: Iterable[
        TaxRecognitionEvent
    ],
) -> tuple[
    Decimal,
    Decimal,
]:
    """
    Derive CURRENT gross INPUT credit from complete immutable
    TaxRecognitionEvent history.

    Reversals are resolved over the full history first by the
    existing INPUT recognition contract.
    """

    try:
        targets = (
            build_current_input_tax_recognition_targets(
                recognition_events
            )
        )
    except (
        InputTaxRecognitionReconciliationError
    ) as exc:
        raise (
            PurchaseReturnInputVatCreditCorrectionReconciliationIntegrityError(
                "INPUT TaxRecognitionEvent history "
                f"is invalid: {exc}"
            )
        ) from exc

    base = sum(
        (
            _amount(
                target.taxable_base,
                field=(
                    "formed INPUT credit "
                    "taxable_base"
                ),
            )
            for target in targets
        ),
        ZERO,
    )

    tax = sum(
        (
            _amount(
                target.tax_amount,
                field=(
                    "formed INPUT credit "
                    "tax_amount"
                ),
            )
            for target in targets
        ),
        ZERO,
    )

    return (
        base,
        tax,
    )


def _active_return_events(
    events: Iterable[
        PurchaseReturnVatAdjustmentEvent
    ],
    *,
    company_id: int,
    tax_calculation_id: int,
    currency_code: str,
) -> tuple[
    PurchaseReturnVatAdjustmentEvent,
    ...,
]:
    rows = tuple(
        events
    )

    for event in rows:
        if (
            event.company_id
            != company_id
            or event.tax_calculation_id
            != tax_calculation_id
        ):
            raise (
                PurchaseReturnInputVatCreditCorrectionReconciliationIntegrityError(
                    "Purchase Return VAT adjustment "
                    "scope mismatch"
                )
            )

        if _currency(
            event.currency_code
        ) != currency_code:
            raise (
                PurchaseReturnInputVatCreditCorrectionReconciliationIntegrityError(
                    "Purchase Return VAT adjustment "
                    "currency mismatch"
                )
            )

        base = _amount(
            event.adjusted_taxable_base,
            field=(
                "Purchase Return adjusted_taxable_base"
            ),
        )

        tax = _amount(
            event.adjusted_tax_amount,
            field=(
                "Purchase Return adjusted_tax_amount"
            ),
        )

        if (
            base == ZERO
            and tax == ZERO
        ):
            raise (
                PurchaseReturnInputVatCreditCorrectionReconciliationIntegrityError(
                    "Persisted Purchase Return VAT adjustment "
                    "cannot be zero-zero"
                )
            )

    active = _active_original_rows(
        rows,
        label=(
            "PurchaseReturnVatAdjustmentEvent"
        ),
        copied_fields=(
            "company_id",
            "purchase_return_recognition_event_id",
            "tax_calculation_id",
            "basis_kind",
            "adjusted_taxable_base",
            "adjusted_tax_amount",
            "currency_code",
        ),
        date_field="adjustment_date",
    )

    seen_prre_ids = set()

    for event in active:
        prre_id = _positive_id(
            event.purchase_return_recognition_event_id,
            field=(
                "purchase_return_recognition_event_id"
            ),
        )

        if prre_id in seen_prre_ids:
            raise (
                PurchaseReturnInputVatCreditCorrectionReconciliationIntegrityError(
                    "One PurchaseReturnRecognitionEvent "
                    "has multiple ACTIVE VAT adjustment "
                    "basis states"
                )
            )

        seen_prre_ids.add(
            prre_id
        )

    return tuple(
        sorted(
            active,
            key=lambda event: (
                event.adjustment_date,
                event.id,
            ),
        )
    )


def _active_correction_map(
    correction_events: Iterable[
        PurchaseReturnInputVatCreditCorrectionEvent
    ],
    *,
    company_id: int,
    tax_calculation_id: int,
    currency_code: str,
    return_events_by_id: dict[
        int,
        PurchaseReturnVatAdjustmentEvent,
    ],
) -> dict[
    int,
    PurchaseReturnInputVatCreditCorrectionEvent,
]:
    rows = tuple(
        correction_events
    )

    for event in rows:
        if (
            event.company_id
            != company_id
            or event.tax_calculation_id
            != tax_calculation_id
        ):
            raise (
                PurchaseReturnInputVatCreditCorrectionReconciliationIntegrityError(
                    "Legal correction scope mismatch"
                )
            )

        if _currency(
            event.currency_code
        ) != currency_code:
            raise (
                PurchaseReturnInputVatCreditCorrectionReconciliationIntegrityError(
                    "Legal correction currency mismatch"
                )
            )

        base = _amount(
            event.reduced_taxable_base,
            field=(
                "legal correction reduced_taxable_base"
            ),
        )

        tax = _amount(
            event.reduced_tax_amount,
            field=(
                "legal correction reduced_tax_amount"
            ),
        )

        if (
            base == ZERO
            and tax == ZERO
        ):
            raise (
                PurchaseReturnInputVatCreditCorrectionReconciliationIntegrityError(
                    "Persisted legal correction "
                    "cannot be zero-zero"
                )
            )

        source_id = _positive_id(
            event.purchase_return_vat_adjustment_event_id,
            field=(
                "purchase_return_vat_adjustment_event_id"
            ),
        )

        source = return_events_by_id.get(
            source_id
        )

        if source is None:
            raise (
                PurchaseReturnInputVatCreditCorrectionReconciliationIntegrityError(
                    "Legal correction references a "
                    "Purchase Return VAT source outside "
                    "its TaxCalculation"
                )
            )

        if source.reversal_of_id is not None:
            raise (
                PurchaseReturnInputVatCreditCorrectionReconciliationIntegrityError(
                    "Legal correction source must be an "
                    "original Purchase Return VAT "
                    "adjustment event"
                )
            )

        if (
            source.company_id
            != event.company_id
            or source.tax_calculation_id
            != event.tax_calculation_id
        ):
            raise (
                PurchaseReturnInputVatCreditCorrectionReconciliationIntegrityError(
                    "Legal correction and Purchase Return "
                    "VAT source mismatch"
                )
            )

    active = _active_original_rows(
        rows,
        label=(
            "PurchaseReturnInputVatCreditCorrectionEvent"
        ),
        copied_fields=(
            "company_id",
            "purchase_return_vat_adjustment_event_id",
            "tax_calculation_id",
            "reduced_taxable_base",
            "reduced_tax_amount",
            "currency_code",
        ),
        date_field="adjustment_date",
    )

    result = {}

    for event in active:
        source_id = (
            event
            .purchase_return_vat_adjustment_event_id
        )

        if source_id in result:
            raise (
                PurchaseReturnInputVatCreditCorrectionReconciliationIntegrityError(
                    "Multiple ACTIVE legal corrections "
                    "for one Purchase Return VAT source"
                )
            )

        result[
            source_id
        ] = event

    return result


def _stable_target_date(
    *,
    target: PurchaseReturnInputVatCreditCorrectionTarget,
    current: (
        PurchaseReturnInputVatCreditCorrectionEvent
        | None
    ),
) -> PurchaseReturnInputVatCreditCorrectionTarget:
    """
    A later no-op reconciliation must not churn immutable history
    merely because the caller supplied a later adjustment_date.

    If desired amounts already equal the active correction, retain
    its historical adjustment_date so persistence sees an exact
    no-op.

    Changed amounts use the new reconciliation adjustment_date.
    """

    if current is None:
        return target

    if (
        _amount(
            current.reduced_taxable_base,
            field=(
                "current correction reduced_taxable_base"
            ),
        )
        == target.reduced_taxable_base
        and _amount(
            current.reduced_tax_amount,
            field=(
                "current correction reduced_tax_amount"
            ),
        )
        == target.reduced_tax_amount
    ):
        return replace(
            target,
            adjustment_date=(
                current.adjustment_date
            ),
        )

    return target


async def _load_locked_calculation(
    db: AsyncSession,
    *,
    company_id: int,
    tax_calculation_id: int,
):
    return (
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


async def _load_locked_recognition_history(
    db: AsyncSession,
    *,
    company_id: int,
    tax_calculation_id: int,
) -> tuple[
    TaxRecognitionEvent,
    ...,
]:
    return tuple(
        (
            await db.execute(
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
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )


async def _load_locked_return_history(
    db: AsyncSession,
    *,
    company_id: int,
    tax_calculation_id: int,
) -> tuple[
    PurchaseReturnVatAdjustmentEvent,
    ...,
]:
    return tuple(
        (
            await db.execute(
                select(
                    PurchaseReturnVatAdjustmentEvent
                )
                .where(
                    PurchaseReturnVatAdjustmentEvent.company_id
                    == company_id,
                    PurchaseReturnVatAdjustmentEvent.tax_calculation_id
                    == tax_calculation_id,
                )
                .order_by(
                    PurchaseReturnVatAdjustmentEvent.id
                )
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )


async def _load_locked_correction_history(
    db: AsyncSession,
    *,
    company_id: int,
    tax_calculation_id: int,
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
                    PurchaseReturnInputVatCreditCorrectionEvent.company_id
                    == company_id,
                    PurchaseReturnInputVatCreditCorrectionEvent.tax_calculation_id
                    == tax_calculation_id,
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


async def reconcile_purchase_return_input_vat_credit_corrections_for_tax_calculation(
    db: AsyncSession,
    *,
    company_id: int,
    tax_calculation_id: int,
    adjustment_date: date,
    created_by: int,
) -> PurchaseReturnInputVatCreditCorrectionReconciliationResult:
    """
    Reconcile buyer-side Purchase Return INPUT VAT credit
    corrections for one TaxCalculation.

    Scope is TaxCalculation-wide because:
    - multiple active return sources share one credit capacity;
    - reversing an earlier return may shift correction capacity
      to later return peers;
    - late credit formation may increase required correction.

    Current INPUT credit is derived from the complete immutable
    TaxRecognitionEvent history first. Reversals are resolved
    before any balance is used.

    This automatic reconciler is forward-only.

    Source processing:
    1. reverse active legal-correction sources whose Purchase
       Return VAT source is no longer active;
    2. process all CURRENT active Purchase Return VAT sources in:
           adjustment_date
           event id
       order;
    3. allocate prior return capacity cumulatively;
    4. persist immutable reversal/replacement changes.

    No JournalEntry is created here.
    No TaxRecognitionEvent is mutated.
    No TaxCreditEvidence is mutated.
    No TaxCalculation is mutated.
    No supplier advance reconciliation occurs.
    No COMMIT / ROLLBACK occurs.
    """

    company_id = _positive_id(
        company_id,
        field="company_id",
    )

    tax_calculation_id = _positive_id(
        tax_calculation_id,
        field="tax_calculation_id",
    )

    created_by = _positive_id(
        created_by,
        field="created_by",
    )

    adjustment_date = _business_date(
        adjustment_date,
        field="adjustment_date",
    )

    calculation = (
        await _load_locked_calculation(
            db,
            company_id=company_id,
            tax_calculation_id=(
                tax_calculation_id
            ),
        )
    )

    (
        calculation_base,
        calculation_tax,
        currency_code,
        calculation_date,
    ) = _validate_calculation(
        calculation,
        company_id=company_id,
        tax_calculation_id=(
            tax_calculation_id
        ),
    )

    recognition_history = (
        await _load_locked_recognition_history(
            db,
            company_id=company_id,
            tax_calculation_id=(
                tax_calculation_id
            ),
        )
    )

    return_history = (
        await _load_locked_return_history(
            db,
            company_id=company_id,
            tax_calculation_id=(
                tax_calculation_id
            ),
        )
    )

    correction_history = (
        await _load_locked_correction_history(
            db,
            company_id=company_id,
            tax_calculation_id=(
                tax_calculation_id
            ),
        )
    )

    _validate_forward_chronology(
        adjustment_date=(
            adjustment_date
        ),
        calculation_date=(
            calculation_date
        ),
        recognition_events=(
            recognition_history
        ),
        return_events=(
            return_history
        ),
        correction_events=(
            correction_history
        ),
    )

    (
        formed_base,
        formed_tax,
    ) = _formed_input_credit(
        recognition_history
    )

    if (
        formed_base
        > calculation_base
        or formed_tax
        > calculation_tax
    ):
        raise (
            PurchaseReturnInputVatCreditCorrectionReconciliationIntegrityError(
                "Formed INPUT credit exceeds "
                "TaxCalculation capacity"
            )
        )

    active_returns = (
        _active_return_events(
            return_history,
            company_id=company_id,
            tax_calculation_id=(
                tax_calculation_id
            ),
            currency_code=(
                currency_code
            ),
        )
    )

    return_by_id = {
        event.id:
        event
        for event in return_history
    }

    current_corrections = (
        _active_correction_map(
            correction_history,
            company_id=company_id,
            tax_calculation_id=(
                tax_calculation_id
            ),
            currency_code=(
                currency_code
            ),
            return_events_by_id=(
                return_by_id
            ),
        )
    )

    active_return_ids = {
        event.id
        for event in active_returns
    }

    current_source_ids = tuple(
        sorted(
            current_corrections
        )
    )

    zeroed_source_ids = tuple(
        sorted(
            set(
                current_corrections
            )
            - active_return_ids
        )
    )

    created = []

    for source_id in zeroed_source_ids:
        zero_target = (
            PurchaseReturnInputVatCreditCorrectionTarget(
                purchase_return_vat_adjustment_event_id=(
                    source_id
                ),
                tax_calculation_id=(
                    tax_calculation_id
                ),
                adjustment_date=(
                    adjustment_date
                ),
                reduced_taxable_base=ZERO,
                reduced_tax_amount=ZERO,
                currency_code=(
                    currency_code
                ),
            )
        )

        try:
            created.extend(
                await reconcile_purchase_return_input_vat_credit_correction_source(
                    db,
                    company_id=company_id,
                    target=zero_target,
                    created_by=created_by,
                    reversal_date=(
                        adjustment_date
                    ),
                )
            )
        except (
            PurchaseReturnInputVatCreditCorrectionPersistenceError
        ) as exc:
            raise (
                PurchaseReturnInputVatCreditCorrectionReconciliationError(
                    "Failed to zero inactive legal "
                    f"correction source {source_id}: {exc}"
                )
            ) from exc

    prior_base = ZERO
    prior_tax = ZERO
    desired_targets = []

    for source in active_returns:
        try:
            target = (
                build_purchase_return_input_vat_credit_correction_target(
                    purchase_return_vat_adjustment_event_id=(
                        source.id
                    ),
                    tax_calculation_id=(
                        tax_calculation_id
                    ),
                    adjustment_date=(
                        adjustment_date
                    ),
                    calculation_taxable_base=(
                        calculation_base
                    ),
                    calculation_tax_amount=(
                        calculation_tax
                    ),
                    formed_credit_taxable_base=(
                        formed_base
                    ),
                    formed_credit_tax_amount=(
                        formed_tax
                    ),
                    prior_active_return_taxable_base=(
                        prior_base
                    ),
                    prior_active_return_tax_amount=(
                        prior_tax
                    ),
                    current_return_taxable_base=(
                        _amount(
                            source.adjusted_taxable_base,
                            field=(
                                "active return "
                                "adjusted_taxable_base"
                            ),
                        )
                    ),
                    current_return_tax_amount=(
                        _amount(
                            source.adjusted_tax_amount,
                            field=(
                                "active return "
                                "adjusted_tax_amount"
                            ),
                        )
                    ),
                    currency_code=(
                        currency_code
                    ),
                )
            )
        except (
            PurchaseReturnInputVatCreditCorrectionCalculationError
        ) as exc:
            raise (
                PurchaseReturnInputVatCreditCorrectionReconciliationIntegrityError(
                    "Unable to calculate legal "
                    f"correction for return source "
                    f"{source.id}: {exc}"
                )
            ) from exc

        target = _stable_target_date(
            target=target,
            current=(
                current_corrections.get(
                    source.id
                )
            ),
        )

        desired_targets.append(
            target
        )

        try:
            created.extend(
                await reconcile_purchase_return_input_vat_credit_correction_source(
                    db,
                    company_id=company_id,
                    target=target,
                    created_by=created_by,
                    reversal_date=(
                        adjustment_date
                    ),
                )
            )
        except (
            PurchaseReturnInputVatCreditCorrectionPersistenceError
        ) as exc:
            raise (
                PurchaseReturnInputVatCreditCorrectionReconciliationError(
                    "Failed to persist legal correction "
                    f"for return source {source.id}: {exc}"
                )
            ) from exc

        prior_base += _amount(
            source.adjusted_taxable_base,
            field=(
                "active return adjusted_taxable_base"
            ),
        )

        prior_tax += _amount(
            source.adjusted_tax_amount,
            field=(
                "active return adjusted_tax_amount"
            ),
        )

    return (
        PurchaseReturnInputVatCreditCorrectionReconciliationResult(
            company_id=company_id,
            tax_calculation_id=(
                tax_calculation_id
            ),
            adjustment_date=(
                adjustment_date
            ),
            calculation_taxable_base=(
                calculation_base
            ),
            calculation_tax_amount=(
                calculation_tax
            ),
            formed_credit_taxable_base=(
                formed_base
            ),
            formed_credit_tax_amount=(
                formed_tax
            ),
            active_return_event_ids=tuple(
                event.id
                for event in active_returns
            ),
            current_correction_source_ids=(
                current_source_ids
            ),
            zeroed_correction_source_ids=(
                zeroed_source_ids
            ),
            desired_targets=tuple(
                desired_targets
            ),
            created_events=tuple(
                created
            ),
        )
    )
