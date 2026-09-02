from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Iterable

from app.services.tax_credit_evidence_persistence_service import (
    TaxCreditEvidenceDataIntegrityError,
    build_tax_credit_evidence_windows,
)
from app.services.tax_recognition_orchestration_service import (
    TaxRecognitionCandidate,
    TaxRecognitionCandidateKind,
)
from app.services.tax_recognition_types import (
    TaxRecognitionMethod,
)
from app.services.tax_types import (
    TaxDirection,
    TaxType,
)


ZERO = Decimal("0")


class InputTaxRecognitionCalculationError(Exception):
    """Base INPUT VAT recognition calculation error."""


class InputTaxRecognitionDataIntegrityError(
    InputTaxRecognitionCalculationError
):
    """Persisted INPUT VAT recognition state is inconsistent."""


class InputTaxRecognitionDirectionError(
    InputTaxRecognitionCalculationError
):
    """TaxCalculation is not INPUT VAT."""


class InputTaxRecognitionMethodError(
    InputTaxRecognitionCalculationError
):
    """Recognition method cannot be automatically calculated."""


class InputTaxRecognitionCandidateError(
    InputTaxRecognitionCalculationError
):
    """Economic recognition candidate state is invalid."""


@dataclass(
    frozen=True,
    slots=True,
)
class InputTaxRecognitionLimit:
    """
    Maximum INPUT VAT recognition legally/economically available
    as of one business date.

    economic_*:
        capacity opened by purchase receipt/payment timing.

    evidence_*:
        capacity supported by active TaxCreditEvidence.

    recognizable_*:
        intersection of economic capacity, evidence capacity,
        and immutable TaxCalculation capacity.
    """

    as_of_date: date

    economic_taxable_base: Decimal
    economic_tax_amount: Decimal

    evidence_taxable_base: Decimal
    evidence_tax_amount: Decimal

    recognizable_taxable_base: Decimal
    recognizable_tax_amount: Decimal

    @property
    def is_zero(
        self,
    ) -> bool:
        return (
            self.recognizable_taxable_base
            == ZERO
            and self.recognizable_tax_amount
            == ZERO
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
        raise InputTaxRecognitionDataIntegrityError(
            f"{field} must be a decimal amount"
        ) from exc

    if not result.is_finite():
        raise InputTaxRecognitionDataIntegrityError(
            f"{field} must be finite"
        )

    return result


def _business_date(
    value,
    *,
    field: str,
) -> date:
    if not isinstance(
        value,
        date,
    ):
        raise InputTaxRecognitionDataIntegrityError(
            f"{field} must be a date"
        )

    return value


def _validate_calculation(
    calculation,
) -> tuple[
    TaxRecognitionMethod,
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
        raise InputTaxRecognitionDataIntegrityError(
            "TaxCalculation must have a positive id"
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
        raise InputTaxRecognitionDataIntegrityError(
            "TaxCalculation must have a positive company_id"
        )

    try:
        tax_type = TaxType(
            calculation.tax_type
        )
    except ValueError as exc:
        raise InputTaxRecognitionDataIntegrityError(
            "Unsupported TaxCalculation tax_type"
        ) from exc

    if tax_type != TaxType.VAT:
        raise InputTaxRecognitionDirectionError(
            "INPUT tax recognition supports VAT only"
        )

    try:
        direction = TaxDirection(
            calculation.direction
        )
    except ValueError as exc:
        raise InputTaxRecognitionDataIntegrityError(
            "Unsupported TaxCalculation direction"
        ) from exc

    if direction != TaxDirection.INPUT:
        raise InputTaxRecognitionDirectionError(
            "INPUT tax recognition requires "
            "TaxDirection.INPUT"
        )

    try:
        method = TaxRecognitionMethod(
            calculation.recognition_method
        )
    except ValueError as exc:
        raise InputTaxRecognitionDataIntegrityError(
            "Unsupported TaxCalculation recognition method"
        ) from exc

    if method == TaxRecognitionMethod.MANUAL:
        raise InputTaxRecognitionMethodError(
            "MANUAL INPUT VAT recognition cannot "
            "be automatically calculated"
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
        raise InputTaxRecognitionDataIntegrityError(
            "TaxCalculation amounts cannot be negative"
        )

    currency_code = str(
        calculation.currency_code
    ).strip().upper()

    if len(currency_code) != 3:
        raise InputTaxRecognitionDataIntegrityError(
            "TaxCalculation currency_code is invalid"
        )

    return (
        method,
        calculated_base,
        calculated_tax,
        currency_code,
    )


def _candidate_kind_order(
    kind: TaxRecognitionCandidateKind,
) -> int:
    if (
        kind
        == TaxRecognitionCandidateKind.FULFILLMENT
    ):
        return 0

    if (
        kind
        == TaxRecognitionCandidateKind.SETTLEMENT
    ):
        return 1

    raise InputTaxRecognitionCandidateError(
        "Unsupported economic candidate kind"
    )


def _economic_capacity(
    *,
    method: TaxRecognitionMethod,
    candidates: Iterable[
        TaxRecognitionCandidate
    ],
    as_of_date: date,
    calculated_base: Decimal,
    calculated_tax: Decimal,
) -> tuple[
    Decimal,
    Decimal,
]:
    candidate_tuple = tuple(
        candidates
    )

    seen = set()

    for candidate in candidate_tuple:
        if not isinstance(
            candidate,
            TaxRecognitionCandidate,
        ):
            raise InputTaxRecognitionCandidateError(
                "Economic candidate must be "
                "TaxRecognitionCandidate"
            )

        if candidate.source_key in seen:
            raise InputTaxRecognitionCandidateError(
                "Duplicate economic recognition candidate"
            )

        seen.add(
            candidate.source_key
        )

    eligible = tuple(
        candidate
        for candidate in candidate_tuple
        if candidate.event_date <= as_of_date
        and (
            method
            == TaxRecognitionMethod.FIRST_EVENT
            or (
                method
                == TaxRecognitionMethod.CASH_METHOD
                and candidate.kind
                == TaxRecognitionCandidateKind.SETTLEMENT
            )
        )
    )

    remaining_base = calculated_base
    remaining_tax = calculated_tax

    economic_base = ZERO
    economic_tax = ZERO

    for candidate in sorted(
        eligible,
        key=lambda item: (
            item.event_date,
            _candidate_kind_order(
                item.kind
            ),
            item.source_id,
        ),
    ):
        if (
            remaining_base == ZERO
            and remaining_tax == ZERO
        ):
            break

        candidate_base = _decimal(
            candidate.taxable_base_capacity,
            field=(
                "Economic candidate "
                "taxable_base_capacity"
            ),
        )

        candidate_tax = _decimal(
            candidate.tax_amount_capacity,
            field=(
                "Economic candidate "
                "tax_amount_capacity"
            ),
        )

        if (
            candidate_base < ZERO
            or candidate_tax < ZERO
        ):
            raise InputTaxRecognitionCandidateError(
                "Economic candidate capacity "
                "cannot be negative"
            )

        allocated_base = min(
            candidate_base,
            remaining_base,
        )

        allocated_tax = min(
            candidate_tax,
            remaining_tax,
        )

        economic_base += allocated_base
        economic_tax += allocated_tax

        remaining_base -= allocated_base
        remaining_tax -= allocated_tax

    return (
        economic_base,
        economic_tax,
    )


def _evidence_capacity(
    *,
    calculation,
    evidence_events: Iterable,
    as_of_date: date,
    calculated_base: Decimal,
    calculated_tax: Decimal,
    currency_code: str,
) -> tuple[
    Decimal,
    Decimal,
]:
    event_tuple = tuple(
        evidence_events
    )

    for event in event_tuple:
        if (
            getattr(
                event,
                "company_id",
                None,
            )
            != calculation.company_id
        ):
            raise InputTaxRecognitionDataIntegrityError(
                "TaxCreditEvidence company does not "
                "match TaxCalculation"
            )

        if (
            getattr(
                event,
                "tax_calculation_id",
                None,
            )
            != calculation.id
        ):
            raise InputTaxRecognitionDataIntegrityError(
                "TaxCreditEvidence TaxCalculation "
                "does not match"
            )

        event_currency = str(
            getattr(
                event,
                "currency_code",
                "",
            )
        ).strip().upper()

        if event_currency != currency_code:
            raise InputTaxRecognitionDataIntegrityError(
                "TaxCreditEvidence currency does not "
                "match TaxCalculation"
            )

    try:
        windows = (
            build_tax_credit_evidence_windows(
                events=event_tuple
            )
        )
    except TaxCreditEvidenceDataIntegrityError as exc:
        raise InputTaxRecognitionDataIntegrityError(
            str(exc)
        ) from exc

    active = tuple(
        window
        for window in windows
        if (
            window.start_date
            <= as_of_date
            and (
                window.end_date is None
                or as_of_date
                < window.end_date
            )
        )
    )

    evidence_base = sum(
        (
            window.evidenced_taxable_base
            for window in active
        ),
        ZERO,
    )

    evidence_tax = sum(
        (
            window.evidenced_tax_amount
            for window in active
        ),
        ZERO,
    )

    if (
        evidence_base > calculated_base
        or evidence_tax > calculated_tax
    ):
        raise InputTaxRecognitionDataIntegrityError(
            "ACTIVE TaxCreditEvidence capacity "
            "exceeds TaxCalculation"
        )

    return (
        evidence_base,
        evidence_tax,
    )


def calculate_input_tax_recognition_limit(
    *,
    calculation,
    economic_candidates: Iterable[
        TaxRecognitionCandidate
    ],
    evidence_events: Iterable,
    as_of_date: date,
) -> InputTaxRecognitionLimit:
    """
    Calculate INPUT VAT recognition limit as the intersection:

        economic timing capacity
        AND active legal evidence capacity
        AND TaxCalculation capacity.

    FIRST_EVENT:
        fulfillment and settlement are eligible economic events.

    CASH_METHOD:
        only payment settlement is an eligible economic event.

    Evidence alone never creates recognition capacity.
    Economic timing alone never creates recognition capacity.
    """

    as_of_date = _business_date(
        as_of_date,
        field="as_of_date",
    )

    (
        method,
        calculated_base,
        calculated_tax,
        currency_code,
    ) = _validate_calculation(
        calculation
    )

    (
        economic_base,
        economic_tax,
    ) = _economic_capacity(
        method=method,
        candidates=economic_candidates,
        as_of_date=as_of_date,
        calculated_base=calculated_base,
        calculated_tax=calculated_tax,
    )

    (
        evidence_base,
        evidence_tax,
    ) = _evidence_capacity(
        calculation=calculation,
        evidence_events=evidence_events,
        as_of_date=as_of_date,
        calculated_base=calculated_base,
        calculated_tax=calculated_tax,
        currency_code=currency_code,
    )

    recognizable_base = min(
        calculated_base,
        economic_base,
        evidence_base,
    )

    recognizable_tax = min(
        calculated_tax,
        economic_tax,
        evidence_tax,
    )

    return InputTaxRecognitionLimit(
        as_of_date=as_of_date,
        economic_taxable_base=(
            economic_base
        ),
        economic_tax_amount=(
            economic_tax
        ),
        evidence_taxable_base=(
            evidence_base
        ),
        evidence_tax_amount=(
            evidence_tax
        ),
        recognizable_taxable_base=(
            recognizable_base
        ),
        recognizable_tax_amount=(
            recognizable_tax
        ),
    )
