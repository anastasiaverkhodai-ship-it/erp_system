from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tax_calculation import TaxCalculation
from app.models.tax_credit_evidence import TaxCreditEvidence
from app.models.tax_recognition_event import TaxRecognitionEvent
from app.services.tax_credit_evidence_persistence_service import (
    TaxCreditEvidenceDataIntegrityError,
    TaxCreditEvidenceWindow,
    build_tax_credit_evidence_windows,
)
from app.services.tax_recognition_types import (
    TaxRecognitionMethod,
)
from app.services.tax_types import (
    TaxDirection,
    TaxType,
)


ZERO = Decimal("0")


class InputTaxRecognitionPersistenceError(Exception):
    """Base automatic INPUT VAT persistence error."""


class InputTaxRecognitionPersistenceStateError(
    InputTaxRecognitionPersistenceError
):
    """Requested INPUT recognition target is invalid."""


class InputTaxRecognitionPersistenceIntegrityError(
    InputTaxRecognitionPersistenceError
):
    """Persisted INPUT recognition/evidence history is corrupt."""


class InputTaxRecognitionPersistenceCapacityError(
    InputTaxRecognitionPersistenceError
):
    """Requested recognition exceeds legal/calculated capacity."""


class InputTaxRecognitionCalculationNotFoundError(
    InputTaxRecognitionPersistenceError
):
    """TaxCalculation does not exist for the company."""


@dataclass(
    frozen=True,
    slots=True,
)
class InputTaxRecognitionPersistenceTarget:
    tax_credit_evidence_id: int
    recognition_date: date
    taxable_base: Decimal
    tax_amount: Decimal

    def __post_init__(
        self,
    ) -> None:
        if (
            isinstance(
                self.tax_credit_evidence_id,
                bool,
            )
            or self.tax_credit_evidence_id <= 0
        ):
            raise (
                InputTaxRecognitionPersistenceStateError(
                    "tax_credit_evidence_id must be "
                    "greater than zero"
                )
            )

        if not isinstance(
            self.recognition_date,
            date,
        ):
            raise (
                InputTaxRecognitionPersistenceStateError(
                    "recognition_date must be a date"
                )
            )

        if (
            self.taxable_base < ZERO
            or self.tax_amount < ZERO
        ):
            raise (
                InputTaxRecognitionPersistenceStateError(
                    "INPUT recognition target "
                    "cannot be negative"
                )
            )

    @property
    def is_zero(
        self,
    ) -> bool:
        return (
            self.taxable_base == ZERO
            and self.tax_amount == ZERO
        )


@dataclass(
    frozen=True,
    slots=True,
)
class InputTaxRecognitionSourcePlan:
    reversal_event_ids: tuple[
        int,
        ...,
    ]
    replacement_target: (
        InputTaxRecognitionPersistenceTarget
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
    try:
        result = Decimal(
            str(value)
        )
    except Exception as exc:
        raise (
            InputTaxRecognitionPersistenceIntegrityError(
                f"{field} must be a decimal amount"
            )
        ) from exc

    if not result.is_finite():
        raise (
            InputTaxRecognitionPersistenceIntegrityError(
                f"{field} must be finite"
            )
        )

    return result


def _positive_id(
    value: int,
    *,
    field: str,
) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value <= 0
    ):
        raise (
            InputTaxRecognitionPersistenceStateError(
                f"{field} must be greater than zero"
            )
        )

    return value


def _validate_calculation(
    calculation,
) -> tuple[
    Decimal,
    Decimal,
    str,
]:
    if (
        getattr(
            calculation,
            "id",
            None,
        )
        is None
        or calculation.id <= 0
    ):
        raise (
            InputTaxRecognitionPersistenceIntegrityError(
                "TaxCalculation must have "
                "a positive id"
            )
        )

    if (
        getattr(
            calculation,
            "company_id",
            None,
        )
        is None
        or calculation.company_id <= 0
    ):
        raise (
            InputTaxRecognitionPersistenceIntegrityError(
                "TaxCalculation must have "
                "a positive company_id"
            )
        )

    try:
        tax_type = TaxType(
            calculation.tax_type
        )
    except ValueError as exc:
        raise (
            InputTaxRecognitionPersistenceIntegrityError(
                "Unsupported TaxCalculation tax_type"
            )
        ) from exc

    if tax_type != TaxType.VAT:
        raise (
            InputTaxRecognitionPersistenceStateError(
                "Automatic INPUT recognition "
                "supports VAT only"
            )
        )

    try:
        direction = TaxDirection(
            calculation.direction
        )
    except ValueError as exc:
        raise (
            InputTaxRecognitionPersistenceIntegrityError(
                "Unsupported TaxCalculation direction"
            )
        ) from exc

    if direction != TaxDirection.INPUT:
        raise (
            InputTaxRecognitionPersistenceStateError(
                "Automatic INPUT recognition requires "
                "TaxDirection.INPUT"
            )
        )

    try:
        method = TaxRecognitionMethod(
            calculation.recognition_method
        )
    except ValueError as exc:
        raise (
            InputTaxRecognitionPersistenceIntegrityError(
                "Unsupported recognition method"
            )
        ) from exc

    if method == TaxRecognitionMethod.MANUAL:
        raise (
            InputTaxRecognitionPersistenceStateError(
                "MANUAL INPUT recognition cannot use "
                "automatic evidence persistence"
            )
        )

    calculated_base = _decimal(
        calculation.taxable_base,
        field="TaxCalculation taxable_base",
    )

    calculated_tax = _decimal(
        calculation.tax_amount,
        field="TaxCalculation tax_amount",
    )

    if (
        calculated_base < ZERO
        or calculated_tax < ZERO
    ):
        raise (
            InputTaxRecognitionPersistenceIntegrityError(
                "TaxCalculation amounts cannot "
                "be negative"
            )
        )

    currency_code = str(
        calculation.currency_code
    ).strip().upper()

    if len(currency_code) != 3:
        raise (
            InputTaxRecognitionPersistenceIntegrityError(
                "TaxCalculation currency_code "
                "is invalid"
            )
        )

    return (
        calculated_base,
        calculated_tax,
        currency_code,
    )


def _validate_evidence_history(
    *,
    calculation,
    evidence_events: tuple,
    currency_code: str,
) -> tuple[
    TaxCreditEvidenceWindow,
    ...,
]:
    for event in evidence_events:
        if (
            getattr(
                event,
                "company_id",
                None,
            )
            != calculation.company_id
        ):
            raise (
                InputTaxRecognitionPersistenceIntegrityError(
                    "TaxCreditEvidence company "
                    "does not match TaxCalculation"
                )
            )

        if (
            getattr(
                event,
                "tax_calculation_id",
                None,
            )
            != calculation.id
        ):
            raise (
                InputTaxRecognitionPersistenceIntegrityError(
                    "TaxCreditEvidence TaxCalculation "
                    "does not match"
                )
            )

        event_currency = str(
            getattr(
                event,
                "currency_code",
                "",
            )
        ).strip().upper()

        if event_currency != currency_code:
            raise (
                InputTaxRecognitionPersistenceIntegrityError(
                    "TaxCreditEvidence currency "
                    "does not match TaxCalculation"
                )
            )

    try:
        return (
            build_tax_credit_evidence_windows(
                events=evidence_events
            )
        )
    except TaxCreditEvidenceDataIntegrityError as exc:
        raise (
            InputTaxRecognitionPersistenceIntegrityError(
                str(exc)
            )
        ) from exc


def _active_recognition_originals(
    *,
    calculation,
    recognition_events: tuple,
    currency_code: str,
) -> tuple:
    by_id = {}

    for event in recognition_events:
        event_id = getattr(
            event,
            "id",
            None,
        )

        if (
            event_id is None
            or event_id <= 0
        ):
            raise (
                InputTaxRecognitionPersistenceIntegrityError(
                    "Persisted TaxRecognitionEvent "
                    "must have a positive id"
                )
            )

        if event_id in by_id:
            raise (
                InputTaxRecognitionPersistenceIntegrityError(
                    "Duplicate TaxRecognitionEvent id"
                )
            )

        if (
            event.company_id
            != calculation.company_id
            or event.tax_calculation_id
            != calculation.id
        ):
            raise (
                InputTaxRecognitionPersistenceIntegrityError(
                    "TaxRecognitionEvent scope "
                    "does not match TaxCalculation"
                )
            )

        event_currency = str(
            event.currency_code
        ).strip().upper()

        if event_currency != currency_code:
            raise (
                InputTaxRecognitionPersistenceIntegrityError(
                    "TaxRecognitionEvent currency "
                    "does not match TaxCalculation"
                )
            )

        by_id[
            event_id
        ] = event

    reversed_ids = set()

    for event in recognition_events:
        reversal_of_id = getattr(
            event,
            "reversal_of_id",
            None,
        )

        if reversal_of_id is None:
            continue

        if reversal_of_id <= 0:
            raise (
                InputTaxRecognitionPersistenceIntegrityError(
                    "reversal_of_id must be positive"
                )
            )

        original = by_id.get(
            reversal_of_id
        )

        if original is None:
            raise (
                InputTaxRecognitionPersistenceIntegrityError(
                    "TaxRecognitionEvent reversal "
                    "references missing original"
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
                InputTaxRecognitionPersistenceIntegrityError(
                    "Recognition reversal cannot "
                    "reverse another reversal"
                )
            )

        if reversal_of_id in reversed_ids:
            raise (
                InputTaxRecognitionPersistenceIntegrityError(
                    "Recognition original has "
                    "multiple reversals"
                )
            )

        copied_fields = (
            "company_id",
            "tax_calculation_id",
            "invoice_fulfillment_allocation_id",
            "payment_settlement_allocation_id",
            "tax_credit_evidence_id",
            "recognized_taxable_base",
            "recognized_tax_amount",
            "currency_code",
        )

        for field in copied_fields:
            if (
                getattr(
                    event,
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
                    InputTaxRecognitionPersistenceIntegrityError(
                        "Recognition reversal must copy "
                        "original source and amounts"
                    )
                )

        if (
            event.recognition_date
            < original.recognition_date
        ):
            raise (
                InputTaxRecognitionPersistenceIntegrityError(
                    "Recognition reversal date "
                    "precedes original"
                )
            )

        reversed_ids.add(
            reversal_of_id
        )

    active = tuple(
        event
        for event in recognition_events
        if (
            getattr(
                event,
                "reversal_of_id",
                None,
            )
            is None
            and event.id
            not in reversed_ids
        )
    )

    seen_tranches = set()

    for event in active:
        evidence_id = getattr(
            event,
            "tax_credit_evidence_id",
            None,
        )

        if (
            evidence_id is None
            or evidence_id <= 0
        ):
            raise (
                InputTaxRecognitionPersistenceIntegrityError(
                    "Automatic INPUT recognition "
                    "must use TaxCreditEvidence source"
                )
            )

        if (
            getattr(
                event,
                "invoice_fulfillment_allocation_id",
                None,
            )
            is not None
            or getattr(
                event,
                "payment_settlement_allocation_id",
                None,
            )
            is not None
        ):
            raise (
                InputTaxRecognitionPersistenceIntegrityError(
                    "INPUT evidence recognition "
                    "cannot have OUTPUT typed source"
                )
            )

        tranche_key = (
            evidence_id,
            event.recognition_date,
        )

        if tranche_key in seen_tranches:
            raise (
                InputTaxRecognitionPersistenceIntegrityError(
                    "Multiple ACTIVE INPUT recognition "
                    "originals for one evidence/date tranche"
                )
            )

        seen_tranches.add(
            tranche_key
        )

        recognized_base = _decimal(
            event.recognized_taxable_base,
            field=(
                "recognized_taxable_base"
            ),
        )

        recognized_tax = _decimal(
            event.recognized_tax_amount,
            field="recognized_tax_amount",
        )

        if (
            recognized_base < ZERO
            or recognized_tax < ZERO
            or (
                recognized_base == ZERO
                and recognized_tax == ZERO
            )
        ):
            raise (
                InputTaxRecognitionPersistenceIntegrityError(
                    "ACTIVE automatic INPUT event "
                    "must contain positive recognition"
                )
            )

    return active


def _window_by_source(
    *,
    windows: tuple[
        TaxCreditEvidenceWindow,
        ...,
    ],
    source_id: int,
) -> TaxCreditEvidenceWindow:
    matches = tuple(
        window
        for window in windows
        if window.event_id == source_id
    )

    if len(matches) != 1:
        raise (
            InputTaxRecognitionPersistenceIntegrityError(
                "TaxCreditEvidence source must resolve "
                "to exactly one original window"
            )
        )

    return matches[0]


def build_input_tax_recognition_source_plan(
    *,
    calculation,
    evidence_events: Iterable,
    recognition_events: Iterable,
    target: InputTaxRecognitionPersistenceTarget,
) -> InputTaxRecognitionSourcePlan:
    """
    Reconcile exactly one TaxCreditEvidence/date tranche.

    Tranche identity:
        (tax_credit_evidence_id, recognition_date)

    Immutable strategy:
    - absent -> positive: create full original;
    - exact current target: no-op;
    - positive -> zero: reverse current original;
    - changed positive: reverse current original and create
      one full replacement.

    Therefore there is at most one ACTIVE automatic INPUT
    recognition original per TaxCreditEvidence/date tranche,
    while one evidence source may legitimately have multiple
    active tranches on different recognition dates.
    """

    (
        calculated_base,
        calculated_tax,
        currency_code,
    ) = _validate_calculation(
        calculation
    )

    evidence_tuple = tuple(
        evidence_events
    )

    recognition_tuple = tuple(
        recognition_events
    )

    windows = _validate_evidence_history(
        calculation=calculation,
        evidence_events=evidence_tuple,
        currency_code=currency_code,
    )

    source_window = _window_by_source(
        windows=windows,
        source_id=(
            target.tax_credit_evidence_id
        ),
    )

    target_base = _decimal(
        target.taxable_base,
        field="target taxable_base",
    )

    target_tax = _decimal(
        target.tax_amount,
        field="target tax_amount",
    )

    if (
        target_base < ZERO
        or target_tax < ZERO
    ):
        raise (
            InputTaxRecognitionPersistenceStateError(
                "Recognition target cannot be negative"
            )
        )

    if (
        target_base > source_window
        .evidenced_taxable_base
        or target_tax
        > source_window.evidenced_tax_amount
    ):
        raise (
            InputTaxRecognitionPersistenceCapacityError(
                "Recognition target exceeds "
                "TaxCreditEvidence capacity"
            )
        )

    if not target.is_zero:
        if (
            target.recognition_date
            < source_window.start_date
        ):
            raise (
                InputTaxRecognitionPersistenceStateError(
                    "Recognition date precedes "
                    "TaxCreditEvidence availability"
                )
            )

        if (
            source_window.end_date
            is not None
            and target.recognition_date
            >= source_window.end_date
        ):
            raise (
                InputTaxRecognitionPersistenceStateError(
                    "Positive recognition target uses "
                    "already-reversed evidence"
                )
            )

    active = _active_recognition_originals(
        calculation=calculation,
        recognition_events=recognition_tuple,
        currency_code=currency_code,
    )

    source_active = tuple(
        event
        for event in active
        if (
            event.tax_credit_evidence_id
            == target.tax_credit_evidence_id
            and event.recognition_date
            == target.recognition_date
        )
    )

    if len(source_active) > 1:
        raise (
            InputTaxRecognitionPersistenceIntegrityError(
                "Multiple ACTIVE events for one "
                "TaxCreditEvidence/date tranche"
            )
        )

    current = (
        source_active[0]
        if source_active
        else None
    )

    current_base = (
        _decimal(
            current.recognized_taxable_base,
            field=(
                "current recognized_taxable_base"
            ),
        )
        if current is not None
        else ZERO
    )

    current_tax = (
        _decimal(
            current.recognized_tax_amount,
            field="current recognized_tax_amount",
        )
        if current is not None
        else ZERO
    )

    source_current_base = sum(
        (
            _decimal(
                event.recognized_taxable_base,
                field=(
                    "source recognized_taxable_base"
                ),
            )
            for event in active
            if (
                event.tax_credit_evidence_id
                == target.tax_credit_evidence_id
            )
        ),
        ZERO,
    )

    source_current_tax = sum(
        (
            _decimal(
                event.recognized_tax_amount,
                field=(
                    "source recognized_tax_amount"
                ),
            )
            for event in active
            if (
                event.tax_credit_evidence_id
                == target.tax_credit_evidence_id
            )
        ),
        ZERO,
    )

    source_after_base = (
        source_current_base
        - current_base
        + target_base
    )

    source_after_tax = (
        source_current_tax
        - current_tax
        + target_tax
    )

    if (
        source_after_base
        > source_window.evidenced_taxable_base
        or source_after_tax
        > source_window.evidenced_tax_amount
    ):
        raise (
            InputTaxRecognitionPersistenceCapacityError(
                "ACTIVE INPUT recognition tranches "
                "exceed TaxCreditEvidence capacity"
            )
        )

    total_current_base = sum(
        (
            _decimal(
                event.recognized_taxable_base,
                field=(
                    "active recognized_taxable_base"
                ),
            )
            for event in active
        ),
        ZERO,
    )

    total_current_tax = sum(
        (
            _decimal(
                event.recognized_tax_amount,
                field=(
                    "active recognized_tax_amount"
                ),
            )
            for event in active
        ),
        ZERO,
    )

    total_after_base = (
        total_current_base
        - current_base
        + target_base
    )

    total_after_tax = (
        total_current_tax
        - current_tax
        + target_tax
    )

    if (
        total_after_base > calculated_base
        or total_after_tax > calculated_tax
    ):
        raise (
            InputTaxRecognitionPersistenceCapacityError(
                "INPUT recognition total after "
                "source reconciliation exceeds "
                "TaxCalculation"
            )
        )

    if current is None:
        if target.is_zero:
            return InputTaxRecognitionSourcePlan(
                reversal_event_ids=(),
                replacement_target=None,
            )

        return InputTaxRecognitionSourcePlan(
            reversal_event_ids=(),
            replacement_target=target,
        )

    if (
        current_base == target_base
        and current_tax == target_tax
        and current.recognition_date
        == target.recognition_date
    ):
        return InputTaxRecognitionSourcePlan(
            reversal_event_ids=(),
            replacement_target=None,
        )

    reversal_ids = (
        current.id,
    )

    if target.is_zero:
        return InputTaxRecognitionSourcePlan(
            reversal_event_ids=(
                reversal_ids
            ),
            replacement_target=None,
        )

    return InputTaxRecognitionSourcePlan(
        reversal_event_ids=(
            reversal_ids
        ),
        replacement_target=target,
    )


async def _lock_calculation(
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
            InputTaxRecognitionCalculationNotFoundError(
                "TaxCalculation not found"
            )
        )

    return calculation


async def _load_locked_evidence_history(
    db: AsyncSession,
    *,
    company_id: int,
    tax_calculation_id: int,
) -> tuple[
    TaxCreditEvidence,
    ...,
]:
    return tuple(
        (
            await db.execute(
                select(
                    TaxCreditEvidence
                )
                .where(
                    TaxCreditEvidence.company_id
                    == company_id,
                    TaxCreditEvidence.tax_calculation_id
                    == tax_calculation_id,
                )
                .order_by(
                    TaxCreditEvidence.id
                )
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )


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


async def reconcile_input_tax_recognition_source(
    db: AsyncSession,
    *,
    company_id: int,
    tax_calculation_id: int,
    target: InputTaxRecognitionPersistenceTarget,
    created_by: int,
    reversal_date: date | None = None,
) -> tuple[
    TaxRecognitionEvent,
    ...,
]:
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

    effective_reversal_date = (
        reversal_date
        if reversal_date is not None
        else target.recognition_date
    )

    if not isinstance(
        effective_reversal_date,
        date,
    ):
        raise (
            InputTaxRecognitionPersistenceStateError(
                "reversal_date must be a date"
            )
        )

    calculation = await _lock_calculation(
        db,
        company_id=company_id,
        tax_calculation_id=(
            tax_calculation_id
        ),
    )

    if calculation.company_id != company_id:
        raise (
            InputTaxRecognitionPersistenceIntegrityError(
                "TaxCalculation company mismatch"
            )
        )

    evidence_events = (
        await _load_locked_evidence_history(
            db,
            company_id=company_id,
            tax_calculation_id=(
                tax_calculation_id
            ),
        )
    )

    recognition_events = (
        await _load_locked_recognition_history(
            db,
            company_id=company_id,
            tax_calculation_id=(
                tax_calculation_id
            ),
        )
    )

    plan = (
        build_input_tax_recognition_source_plan(
            calculation=calculation,
            evidence_events=evidence_events,
            recognition_events=(
                recognition_events
            ),
            target=target,
        )
    )

    if plan.is_noop:
        return ()

    by_id = {
        event.id: event
        for event in recognition_events
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
                InputTaxRecognitionPersistenceIntegrityError(
                    "Recognition event selected for "
                    "reversal does not exist"
                )
            )

        if (
            effective_reversal_date
            < original.recognition_date
        ):
            raise (
                InputTaxRecognitionPersistenceStateError(
                    "reversal_date cannot precede "
                    "original recognition_date"
                )
            )

        reversal = TaxRecognitionEvent(
            company_id=company_id,
            tax_calculation_id=(
                tax_calculation_id
            ),
            invoice_fulfillment_allocation_id=None,
            payment_settlement_allocation_id=None,
            tax_credit_evidence_id=(
                original.tax_credit_evidence_id
            ),
            recognition_date=(
                effective_reversal_date
            ),
            recognized_taxable_base=(
                original.recognized_taxable_base
            ),
            recognized_tax_amount=(
                original.recognized_tax_amount
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

    replacement = (
        plan.replacement_target
    )

    if replacement is not None:
        increment = TaxRecognitionEvent(
            company_id=company_id,
            tax_calculation_id=(
                tax_calculation_id
            ),
            invoice_fulfillment_allocation_id=None,
            payment_settlement_allocation_id=None,
            tax_credit_evidence_id=(
                replacement
                .tax_credit_evidence_id
            ),
            recognition_date=(
                replacement.recognition_date
            ),
            recognized_taxable_base=(
                replacement.taxable_base
            ),
            recognized_tax_amount=(
                replacement.tax_amount
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
