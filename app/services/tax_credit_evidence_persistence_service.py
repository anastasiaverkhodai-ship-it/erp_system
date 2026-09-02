from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tax_calculation import TaxCalculation
from app.models.tax_credit_evidence import TaxCreditEvidence
from app.services.tax_credit_evidence_types import (
    TaxCreditEvidenceType,
)
from app.services.tax_types import (
    TaxDirection,
    TaxType,
)


ZERO = Decimal("0.00")
MONEY_QUANTUM = Decimal("0.01")


class TaxCreditEvidencePersistenceError(Exception):
    """Base TaxCreditEvidence persistence error."""


class TaxCreditEvidenceValidationError(
    TaxCreditEvidencePersistenceError
):
    """Input evidence payload is invalid."""


class TaxCreditEvidenceCalculationNotFoundError(
    TaxCreditEvidencePersistenceError
):
    """Required TaxCalculation does not exist."""


class TaxCreditEvidenceDirectionError(
    TaxCreditEvidencePersistenceError
):
    """TaxCalculation is not INPUT VAT."""


class TaxCreditEvidenceCurrencyError(
    TaxCreditEvidencePersistenceError
):
    """Evidence currency does not match TaxCalculation."""


class TaxCreditEvidenceCapacityError(
    TaxCreditEvidencePersistenceError
):
    """Evidence would exceed immutable TaxCalculation capacity."""


class TaxCreditEvidenceDuplicateError(
    TaxCreditEvidencePersistenceError
):
    """Evidence source overlaps an existing immutable source."""


class TaxCreditEvidenceNotFoundError(
    TaxCreditEvidencePersistenceError
):
    """Evidence row does not exist."""


class TaxCreditEvidenceReversalError(
    TaxCreditEvidencePersistenceError
):
    """Evidence cannot be reversed in the requested state."""


class TaxCreditEvidenceDataIntegrityError(
    TaxCreditEvidencePersistenceError
):
    """Persisted TaxCreditEvidence history is inconsistent."""


@dataclass(
    frozen=True,
    slots=True,
)
class TaxCreditEvidenceTarget:
    evidence_type: TaxCreditEvidenceType
    evidence_number: str
    evidence_date: date
    credit_available_date: date
    effective_date: date
    evidenced_taxable_base: Decimal
    evidenced_tax_amount: Decimal
    currency_code: str


@dataclass(
    frozen=True,
    slots=True,
)
class TaxCreditEvidenceWindow:
    event_id: int
    evidence_type: TaxCreditEvidenceType
    evidence_number: str
    start_date: date
    end_date: date | None
    evidenced_taxable_base: Decimal
    evidenced_tax_amount: Decimal


def _money(
    value: Decimal,
    *,
    field: str,
) -> Decimal:
    try:
        result = Decimal(
            str(value)
        )
    except Exception as exc:
        raise TaxCreditEvidenceValidationError(
            f"{field} must be a decimal amount"
        ) from exc

    if not result.is_finite():
        raise TaxCreditEvidenceValidationError(
            f"{field} must be finite"
        )

    return result.quantize(
        MONEY_QUANTUM
    )


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
        raise TaxCreditEvidenceValidationError(
            f"{field} must be greater than zero"
        )

    return value


def _business_date(
    value: date,
    *,
    field: str,
) -> date:
    if (
        not isinstance(value, date)
        or isinstance(value, bool)
    ):
        raise TaxCreditEvidenceValidationError(
            f"{field} must be a date"
        )

    return value


def _currency(
    value: str,
) -> str:
    if not isinstance(value, str):
        raise TaxCreditEvidenceValidationError(
            "currency_code must be a string"
        )

    result = value.strip().upper()

    if len(result) != 3:
        raise TaxCreditEvidenceValidationError(
            "currency_code must contain exactly 3 characters"
        )

    return result


def _evidence_number(
    value: str,
) -> str:
    if not isinstance(value, str):
        raise TaxCreditEvidenceValidationError(
            "evidence_number must be a string"
        )

    result = value.strip()

    if not result:
        raise TaxCreditEvidenceValidationError(
            "evidence_number cannot be empty"
        )

    if len(result) > 120:
        raise TaxCreditEvidenceValidationError(
            "evidence_number cannot exceed 120 characters"
        )

    return result


def build_tax_credit_evidence_target(
    *,
    evidence_type: TaxCreditEvidenceType,
    evidence_number: str,
    evidence_date: date,
    credit_available_date: date,
    evidenced_taxable_base: Decimal,
    evidenced_tax_amount: Decimal,
    currency_code: str,
) -> TaxCreditEvidenceTarget:
    if not isinstance(
        evidence_type,
        TaxCreditEvidenceType,
    ):
        raise TaxCreditEvidenceValidationError(
            "evidence_type must be TaxCreditEvidenceType"
        )

    evidence_date = _business_date(
        evidence_date,
        field="evidence_date",
    )

    credit_available_date = _business_date(
        credit_available_date,
        field="credit_available_date",
    )

    if (
        credit_available_date
        < evidence_date
    ):
        raise TaxCreditEvidenceValidationError(
            "credit_available_date cannot precede evidence_date"
        )

    taxable_base = _money(
        evidenced_taxable_base,
        field="evidenced_taxable_base",
    )

    tax_amount = _money(
        evidenced_tax_amount,
        field="evidenced_tax_amount",
    )

    if taxable_base < ZERO:
        raise TaxCreditEvidenceValidationError(
            "evidenced_taxable_base cannot be negative"
        )

    if tax_amount <= ZERO:
        raise TaxCreditEvidenceValidationError(
            "evidenced_tax_amount must be greater than zero"
        )

    return TaxCreditEvidenceTarget(
        evidence_type=evidence_type,
        evidence_number=_evidence_number(
            evidence_number
        ),
        evidence_date=evidence_date,
        credit_available_date=credit_available_date,
        effective_date=credit_available_date,
        evidenced_taxable_base=taxable_base,
        evidenced_tax_amount=tax_amount,
        currency_code=_currency(
            currency_code
        ),
    )


def _event_id(
    event: TaxCreditEvidence,
) -> int:
    if (
        event.id is None
        or event.id <= 0
    ):
        raise TaxCreditEvidenceDataIntegrityError(
            "Persisted evidence must have a positive id"
        )

    return event.id


def build_tax_credit_evidence_windows(
    *,
    events: tuple[
        TaxCreditEvidence,
        ...,
    ],
) -> tuple[
    TaxCreditEvidenceWindow,
    ...,
]:
    by_id: dict[
        int,
        TaxCreditEvidence,
    ] = {}

    reversals: dict[
        int,
        TaxCreditEvidence,
    ] = {}

    originals: list[
        TaxCreditEvidence
    ] = []

    for event in events:
        event_id = _event_id(
            event
        )

        if event_id in by_id:
            raise TaxCreditEvidenceDataIntegrityError(
                "Duplicate TaxCreditEvidence id in history"
            )

        by_id[event_id] = event

        if event.reversal_of_id is None:
            originals.append(
                event
            )
            continue

        if event.reversal_of_id <= 0:
            raise TaxCreditEvidenceDataIntegrityError(
                "reversal_of_id must be positive"
            )

        if event.reversal_of_id in reversals:
            raise TaxCreditEvidenceDataIntegrityError(
                "Evidence has more than one reversal"
            )

        reversals[
            event.reversal_of_id
        ] = event

    windows = []

    for original in originals:
        original_id = _event_id(
            original
        )

        reversal = reversals.get(
            original_id
        )

        try:
            evidence_type = TaxCreditEvidenceType(
                original.evidence_type
            )
        except ValueError as exc:
            raise TaxCreditEvidenceDataIntegrityError(
                "Persisted evidence_type is unsupported"
            ) from exc

        start_date = _business_date(
            original.effective_date,
            field="effective_date",
        )

        taxable_base = _money(
            original.evidenced_taxable_base,
            field="evidenced_taxable_base",
        )

        tax_amount = _money(
            original.evidenced_tax_amount,
            field="evidenced_tax_amount",
        )

        if (
            taxable_base < ZERO
            or tax_amount <= ZERO
        ):
            raise TaxCreditEvidenceDataIntegrityError(
                "Persisted evidence amounts are invalid"
            )

        end_date = None

        if reversal is not None:
            if (
                reversal.reversal_of_id
                != original_id
            ):
                raise TaxCreditEvidenceDataIntegrityError(
                    "Reversal source identity is inconsistent"
                )

            if (
                reversal.tax_calculation_id
                != original.tax_calculation_id
                or reversal.company_id
                != original.company_id
            ):
                raise TaxCreditEvidenceDataIntegrityError(
                    "Reversal company or TaxCalculation differs"
                )

            copied_fields = (
                "evidence_type",
                "evidence_number",
                "evidence_date",
                "credit_available_date",
                "evidenced_taxable_base",
                "evidenced_tax_amount",
                "currency_code",
            )

            for field in copied_fields:
                if (
                    getattr(reversal, field)
                    != getattr(original, field)
                ):
                    raise TaxCreditEvidenceDataIntegrityError(
                        "Reversal must copy original evidence provenance"
                    )

            end_date = _business_date(
                reversal.effective_date,
                field="reversal effective_date",
            )

            if end_date < start_date:
                raise TaxCreditEvidenceDataIntegrityError(
                    "Reversal effective_date precedes original evidence"
                )

        windows.append(
            TaxCreditEvidenceWindow(
                event_id=original_id,
                evidence_type=evidence_type,
                evidence_number=_evidence_number(
                    original.evidence_number
                ),
                start_date=start_date,
                end_date=end_date,
                evidenced_taxable_base=taxable_base,
                evidenced_tax_amount=tax_amount,
            )
        )

    dangling = (
        set(reversals)
        - {
            item.event_id
            for item in windows
        }
    )

    if dangling:
        raise TaxCreditEvidenceDataIntegrityError(
            "Evidence reversal points to a missing or non-original row"
        )

    return tuple(
        sorted(
            windows,
            key=lambda item: (
                item.start_date,
                item.event_id,
            ),
        )
    )


def _window_active(
    window: TaxCreditEvidenceWindow,
    *,
    on_date: date,
) -> bool:
    if window.start_date > on_date:
        return False

    if (
        window.end_date is not None
        and window.end_date <= on_date
    ):
        return False

    return True


def validate_new_tax_credit_evidence_against_history(
    *,
    target: TaxCreditEvidenceTarget,
    history: tuple[
        TaxCreditEvidence,
        ...,
    ],
    calculation_taxable_base: Decimal,
    calculation_tax_amount: Decimal,
) -> None:
    capacity_base = _money(
        calculation_taxable_base,
        field="calculation_taxable_base",
    )

    capacity_tax = _money(
        calculation_tax_amount,
        field="calculation_tax_amount",
    )

    if capacity_base < ZERO:
        raise TaxCreditEvidenceDataIntegrityError(
            "TaxCalculation taxable_base cannot be negative"
        )

    if capacity_tax <= ZERO:
        raise TaxCreditEvidenceCapacityError(
            "TaxCalculation has no positive INPUT VAT capacity"
        )

    if (
        target.evidenced_taxable_base
        > capacity_base
    ):
        raise TaxCreditEvidenceCapacityError(
            "Evidence taxable base exceeds TaxCalculation capacity"
        )

    if (
        target.evidenced_tax_amount
        > capacity_tax
    ):
        raise TaxCreditEvidenceCapacityError(
            "Evidence tax amount exceeds TaxCalculation capacity"
        )

    windows = build_tax_credit_evidence_windows(
        events=history
    )

    for window in windows:
        if (
            window.evidence_type
            == target.evidence_type
            and window.evidence_number
            == target.evidence_number
            and (
                window.end_date is None
                or window.end_date
                > target.effective_date
            )
        ):
            raise TaxCreditEvidenceDuplicateError(
                "Evidence source overlaps existing immutable history"
            )

    timeline_dates = {
        target.effective_date,
    }

    for window in windows:
        if (
            window.start_date
            >= target.effective_date
        ):
            timeline_dates.add(
                window.start_date
            )

        if (
            window.end_date is not None
            and window.end_date
            >= target.effective_date
        ):
            timeline_dates.add(
                window.end_date
            )

    for on_date in sorted(
        timeline_dates
    ):
        active_base = ZERO
        active_tax = ZERO

        for window in windows:
            if _window_active(
                window,
                on_date=on_date,
            ):
                active_base += (
                    window.evidenced_taxable_base
                )
                active_tax += (
                    window.evidenced_tax_amount
                )

        active_base += (
            target.evidenced_taxable_base
        )

        active_tax += (
            target.evidenced_tax_amount
        )

        if active_base > capacity_base:
            raise TaxCreditEvidenceCapacityError(
                "ACTIVE evidence taxable-base capacity "
                "would exceed TaxCalculation"
            )

        if active_tax > capacity_tax:
            raise TaxCreditEvidenceCapacityError(
                "ACTIVE evidence VAT capacity "
                "would exceed TaxCalculation"
            )


def validate_input_vat_tax_calculation(
    *,
    calculation: TaxCalculation,
    company_id: int,
    target: TaxCreditEvidenceTarget,
) -> None:
    if (
        calculation.id is None
        or calculation.id <= 0
    ):
        raise TaxCreditEvidenceDataIntegrityError(
            "TaxCalculation must have a positive id"
        )

    if calculation.company_id != company_id:
        raise TaxCreditEvidenceDataIntegrityError(
            "TaxCalculation company does not match request"
        )

    try:
        tax_type = TaxType(
            calculation.tax_type
        )
    except ValueError as exc:
        raise TaxCreditEvidenceDataIntegrityError(
            "Unsupported TaxCalculation tax_type"
        ) from exc

    if tax_type != TaxType.VAT:
        raise TaxCreditEvidenceDirectionError(
            "TaxCreditEvidence supports VAT only"
        )

    try:
        direction = TaxDirection(
            calculation.direction
        )
    except ValueError as exc:
        raise TaxCreditEvidenceDataIntegrityError(
            "Unsupported TaxCalculation direction"
        ) from exc

    if direction != TaxDirection.INPUT:
        raise TaxCreditEvidenceDirectionError(
            "TaxCreditEvidence requires INPUT VAT TaxCalculation"
        )

    calculation_currency = _currency(
        calculation.currency_code
    )

    if (
        target.currency_code
        != calculation_currency
    ):
        raise TaxCreditEvidenceCurrencyError(
            "Evidence currency does not match TaxCalculation"
        )


async def _load_locked_calculation(
    db: AsyncSession,
    *,
    company_id: int,
    tax_calculation_id: int,
) -> TaxCalculation:
    calculation = (
        await db.execute(
            select(
                TaxCalculation
            ).where(
                TaxCalculation.company_id
                == company_id,
                TaxCalculation.id
                == tax_calculation_id,
            ).with_for_update()
        )
    ).scalar_one_or_none()

    if calculation is None:
        raise TaxCreditEvidenceCalculationNotFoundError(
            "TaxCalculation was not found for company"
        )

    return calculation


async def _load_locked_history(
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
                ).where(
                    TaxCreditEvidence.company_id
                    == company_id,
                    TaxCreditEvidence.tax_calculation_id
                    == tax_calculation_id,
                ).order_by(
                    TaxCreditEvidence.id
                ).with_for_update()
            )
        ).scalars().all()
    )


async def create_tax_credit_evidence(
    db: AsyncSession,
    *,
    company_id: int,
    tax_calculation_id: int,
    evidence_type: TaxCreditEvidenceType,
    evidence_number: str,
    evidence_date: date,
    credit_available_date: date,
    evidenced_taxable_base: Decimal,
    evidenced_tax_amount: Decimal,
    currency_code: str,
    created_by: int,
) -> TaxCreditEvidence:
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

    target = build_tax_credit_evidence_target(
        evidence_type=evidence_type,
        evidence_number=evidence_number,
        evidence_date=evidence_date,
        credit_available_date=credit_available_date,
        evidenced_taxable_base=evidenced_taxable_base,
        evidenced_tax_amount=evidenced_tax_amount,
        currency_code=currency_code,
    )

    calculation = await _load_locked_calculation(
        db,
        company_id=company_id,
        tax_calculation_id=tax_calculation_id,
    )

    validate_input_vat_tax_calculation(
        calculation=calculation,
        company_id=company_id,
        target=target,
    )

    history = await _load_locked_history(
        db,
        company_id=company_id,
        tax_calculation_id=tax_calculation_id,
    )

    validate_new_tax_credit_evidence_against_history(
        target=target,
        history=history,
        calculation_taxable_base=(
            calculation.taxable_base
        ),
        calculation_tax_amount=(
            calculation.tax_amount
        ),
    )

    event = TaxCreditEvidence(
        company_id=company_id,
        tax_calculation_id=tax_calculation_id,
        evidence_type=(
            target.evidence_type.value
        ),
        evidence_number=(
            target.evidence_number
        ),
        evidence_date=(
            target.evidence_date
        ),
        credit_available_date=(
            target.credit_available_date
        ),
        effective_date=(
            target.effective_date
        ),
        evidenced_taxable_base=(
            target.evidenced_taxable_base
        ),
        evidenced_tax_amount=(
            target.evidenced_tax_amount
        ),
        currency_code=(
            target.currency_code
        ),
        created_by=created_by,
        reversal_of_id=None,
    )

    db.add(
        event
    )

    await db.flush()

    return event


async def reverse_tax_credit_evidence(
    db: AsyncSession,
    *,
    company_id: int,
    tax_credit_evidence_id: int,
    reversal_date: date,
    reversed_by: int,
) -> TaxCreditEvidence:
    company_id = _positive_id(
        company_id,
        field="company_id",
    )

    tax_credit_evidence_id = _positive_id(
        tax_credit_evidence_id,
        field="tax_credit_evidence_id",
    )

    reversed_by = _positive_id(
        reversed_by,
        field="reversed_by",
    )

    reversal_date = _business_date(
        reversal_date,
        field="reversal_date",
    )

    identity_tax_calculation_id = (
        await db.execute(
            select(
                TaxCreditEvidence.tax_calculation_id
            ).where(
                TaxCreditEvidence.company_id
                == company_id,
                TaxCreditEvidence.id
                == tax_credit_evidence_id,
            )
        )
    ).scalar_one_or_none()

    if identity_tax_calculation_id is None:
        original = None
        history = ()
    else:
        await _load_locked_calculation(
            db,
            company_id=company_id,
            tax_calculation_id=(
                identity_tax_calculation_id
            ),
        )

        history = (
            await _load_locked_history(
                db,
                company_id=company_id,
                tax_calculation_id=(
                    identity_tax_calculation_id
                ),
            )
        )

        original = next(
            (
                event
                for event in history
                if event.id
                == tax_credit_evidence_id
            ),
            None,
        )

    if original is None:
        raise TaxCreditEvidenceNotFoundError(
            "TaxCreditEvidence was not found for company"
        )

    if original.reversal_of_id is not None:
        raise TaxCreditEvidenceReversalError(
            "A reversal evidence row cannot itself be reversed"
        )

    existing_reversal = next(
        (
            event.id
            for event in history
            if event.reversal_of_id
            == tax_credit_evidence_id
        ),
        None,
    )

    if existing_reversal is not None:
        raise TaxCreditEvidenceReversalError(
            "TaxCreditEvidence is already reversed"
        )

    if (
        reversal_date
        < original.effective_date
    ):
        raise TaxCreditEvidenceReversalError(
            "reversal_date cannot precede original effective_date"
        )

    reversal = TaxCreditEvidence(
        company_id=original.company_id,
        tax_calculation_id=(
            original.tax_calculation_id
        ),
        evidence_type=(
            original.evidence_type
        ),
        evidence_number=(
            original.evidence_number
        ),
        evidence_date=(
            original.evidence_date
        ),
        credit_available_date=(
            original.credit_available_date
        ),
        effective_date=reversal_date,
        evidenced_taxable_base=(
            original.evidenced_taxable_base
        ),
        evidenced_tax_amount=(
            original.evidenced_tax_amount
        ),
        currency_code=(
            original.currency_code
        ),
        created_by=reversed_by,
        reversal_of_id=original.id,
    )

    db.add(
        reversal
    )

    await db.flush()

    return reversal
