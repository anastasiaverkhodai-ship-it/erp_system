from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Iterable

from app.services.money_rounding import (
    round_currency_amount,
)
from app.services.tax_recognition_persistence_service import (
    TaxRecognitionDataIntegrityError,
    TaxRecognitionInputEvidenceRequiredError,
    TaxRecognitionSourceMethodError,
)
from app.services.tax_recognition_types import (
    TaxRecognitionMethod,
)
from app.services.tax_types import (
    TaxDirection,
)


ZERO = Decimal("0")
ONE = Decimal("1")


class TaxRecognitionOrchestrationError(Exception):
    """Base OUTPUT VAT recognition orchestration error."""


class TaxRecognitionCandidateError(
    TaxRecognitionOrchestrationError
):
    """Recognition candidate is invalid."""


class DuplicateTaxRecognitionSourceError(
    TaxRecognitionOrchestrationError
):
    """The same typed source was supplied more than once."""


class TaxRecognitionTargetStateError(
    TaxRecognitionOrchestrationError
):
    """Current/desired source target state is inconsistent."""


class TaxRecognitionCandidateKind(StrEnum):
    FULFILLMENT = "fulfillment"
    SETTLEMENT = "settlement"


@dataclass(
    frozen=True,
    slots=True,
)
class TaxRecognitionCandidate:
    kind: TaxRecognitionCandidateKind
    source_id: int
    event_date: date
    taxable_base_capacity: Decimal
    tax_amount_capacity: Decimal

    def __post_init__(self) -> None:
        if self.source_id <= 0:
            raise TaxRecognitionCandidateError(
                "Recognition source ID must be "
                "greater than zero"
            )

        if (
            self.taxable_base_capacity < ZERO
            or self.tax_amount_capacity < ZERO
        ):
            raise TaxRecognitionCandidateError(
                "Recognition source capacity "
                "cannot be negative"
            )

    @property
    def source_key(
        self,
    ) -> tuple[
        TaxRecognitionCandidateKind,
        int,
    ]:
        return (
            self.kind,
            self.source_id,
        )


@dataclass(
    frozen=True,
    slots=True,
)
class TaxRecognitionSourceTarget:
    kind: TaxRecognitionCandidateKind
    source_id: int
    event_date: date
    taxable_base: Decimal
    tax_amount: Decimal

    def __post_init__(self) -> None:
        if self.source_id <= 0:
            raise TaxRecognitionTargetStateError(
                "Recognition target source ID "
                "must be greater than zero"
            )

        if (
            self.taxable_base < ZERO
            or self.tax_amount < ZERO
        ):
            raise TaxRecognitionTargetStateError(
                "Recognition target cannot "
                "be negative"
            )

    @property
    def source_key(
        self,
    ) -> tuple[
        TaxRecognitionCandidateKind,
        int,
    ]:
        return (
            self.kind,
            self.source_id,
        )

    @property
    def is_zero(
        self,
    ) -> bool:
        return (
            self.taxable_base == ZERO
            and self.tax_amount == ZERO
        )


def _decimal(
    value,
) -> Decimal:
    return Decimal(value)


def _round_money(
    *,
    amount: Decimal,
    currency_code: str,
) -> Decimal:
    return round_currency_amount(
        amount=amount,
        currency_code=currency_code,
    )


def _kind_order(
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

    raise TaxRecognitionCandidateError(
        "Unsupported recognition candidate kind"
    )


def _candidate_sort_key(
    candidate: TaxRecognitionCandidate,
) -> tuple[
    date,
    int,
    int,
]:
    return (
        candidate.event_date,
        _kind_order(
            candidate.kind
        ),
        candidate.source_id,
    )


def _target_sort_key(
    target: TaxRecognitionSourceTarget,
) -> tuple[
    date,
    int,
    int,
]:
    return (
        target.event_date,
        _kind_order(
            target.kind
        ),
        target.source_id,
    )


def _validate_output_calculation(
    calculation,
) -> TaxRecognitionMethod:
    try:
        direction = TaxDirection(
            calculation.direction
        )
    except ValueError as exc:
        raise TaxRecognitionDataIntegrityError(
            "Unsupported TaxCalculation direction"
        ) from exc

    if (
        direction
        != TaxDirection.OUTPUT
    ):
        raise (
            TaxRecognitionInputEvidenceRequiredError(
                "INPUT VAT recognition requires "
                "tax-credit evidence"
            )
        )

    try:
        method = TaxRecognitionMethod(
            calculation.recognition_method
        )
    except ValueError as exc:
        raise TaxRecognitionDataIntegrityError(
            "Unsupported TaxCalculation "
            "recognition method"
        ) from exc

    if (
        method
        == TaxRecognitionMethod.MANUAL
    ):
        raise TaxRecognitionSourceMethodError(
            "MANUAL recognition cannot be "
            "automatically orchestrated"
        )

    return method


def _validate_ratio(
    *,
    numerator: Decimal,
    denominator: Decimal,
    label: str,
) -> Decimal:
    numerator = _decimal(
        numerator
    )

    denominator = _decimal(
        denominator
    )

    if denominator <= ZERO:
        raise TaxRecognitionCandidateError(
            f"{label} denominator must be "
            "greater than zero"
        )

    if numerator <= ZERO:
        raise TaxRecognitionCandidateError(
            f"{label} numerator must be "
            "greater than zero"
        )

    if numerator > denominator:
        raise TaxRecognitionCandidateError(
            f"{label} numerator cannot exceed "
            "denominator"
        )

    ratio = (
        numerator
        / denominator
    )

    if (
        ratio <= ZERO
        or ratio > ONE
    ):
        raise TaxRecognitionCandidateError(
            f"{label} ratio is outside "
            "the valid range"
        )

    return ratio


def build_fulfillment_recognition_candidate(
    *,
    calculation,
    source_id: int,
    event_date: date,
    allocation_quantity: Decimal,
    invoice_line_quantity: Decimal,
) -> TaxRecognitionCandidate:
    """
    Build the VAT capacity represented by one ACTIVE
    InvoiceFulfillmentAllocation.

    Capacity is proportional to the immutable invoice-line
    TaxCalculation snapshot.
    """

    ratio = _validate_ratio(
        numerator=allocation_quantity,
        denominator=invoice_line_quantity,
        label="Fulfillment allocation",
    )

    currency_code = str(
        calculation.currency_code
    )

    base = _round_money(
        amount=(
            _decimal(
                calculation.taxable_base
            )
            * ratio
        ),
        currency_code=currency_code,
    )

    tax = _round_money(
        amount=(
            _decimal(
                calculation.tax_amount
            )
            * ratio
        ),
        currency_code=currency_code,
    )

    return TaxRecognitionCandidate(
        kind=(
            TaxRecognitionCandidateKind
            .FULFILLMENT
        ),
        source_id=source_id,
        event_date=event_date,
        taxable_base_capacity=base,
        tax_amount_capacity=tax,
    )


def build_settlement_recognition_candidate(
    *,
    calculation,
    source_id: int,
    event_date: date,
    settlement_amount: Decimal,
    invoice_total_amount: Decimal,
) -> TaxRecognitionCandidate:
    """
    Build one payment-settlement VAT capacity.

    PaymentSettlementAllocation is invoice-level rather
    than invoice-line-level. Therefore the settlement
    ratio is measured against the full immutable invoice
    obligation and applied proportionally to each
    TaxCalculation snapshot.
    """

    ratio = _validate_ratio(
        numerator=settlement_amount,
        denominator=invoice_total_amount,
        label="Payment settlement",
    )

    currency_code = str(
        calculation.currency_code
    )

    base = _round_money(
        amount=(
            _decimal(
                calculation.taxable_base
            )
            * ratio
        ),
        currency_code=currency_code,
    )

    tax = _round_money(
        amount=(
            _decimal(
                calculation.tax_amount
            )
            * ratio
        ),
        currency_code=currency_code,
    )

    return TaxRecognitionCandidate(
        kind=(
            TaxRecognitionCandidateKind
            .SETTLEMENT
        ),
        source_id=source_id,
        event_date=event_date,
        taxable_base_capacity=base,
        tax_amount_capacity=tax,
    )


def _deduplicate_candidates(
    candidates: Iterable[
        TaxRecognitionCandidate
    ],
) -> tuple[
    TaxRecognitionCandidate,
    ...,
]:
    result = tuple(
        candidates
    )

    seen = set()

    for candidate in result:
        if (
            candidate.source_key
            in seen
        ):
            raise (
                DuplicateTaxRecognitionSourceError(
                    "Duplicate typed recognition "
                    "candidate"
                )
            )

        seen.add(
            candidate.source_key
        )

    return result


def build_output_tax_recognition_targets(
    *,
    calculation,
    candidates: Iterable[
        TaxRecognitionCandidate
    ],
) -> tuple[
    TaxRecognitionSourceTarget,
    ...,
]:
    """
    Allocate OUTPUT VAT recognition to economic events.

    FIRST_EVENT:
        fulfillment and settlement candidates compete
        chronologically for the remaining TaxCalculation.

    CASH_METHOD:
        only settlement candidates are eligible.

    Same-date events use a deterministic internal
    tie-breaker. This affects source attribution only;
    the legal recognition date remains the same date.
    """

    method = _validate_output_calculation(
        calculation
    )

    all_candidates = (
        _deduplicate_candidates(
            candidates
        )
    )

    if (
        method
        == TaxRecognitionMethod.FIRST_EVENT
    ):
        eligible = all_candidates

    elif (
        method
        == TaxRecognitionMethod.CASH_METHOD
    ):
        eligible = tuple(
            candidate
            for candidate
            in all_candidates
            if (
                candidate.kind
                == TaxRecognitionCandidateKind
                .SETTLEMENT
            )
        )

    else:
        raise TaxRecognitionSourceMethodError(
            "Unsupported automatic recognition "
            "method"
        )

    calculated_base = _decimal(
        calculation.taxable_base
    )

    calculated_tax = _decimal(
        calculation.tax_amount
    )

    if (
        calculated_base < ZERO
        or calculated_tax < ZERO
    ):
        raise TaxRecognitionDataIntegrityError(
            "TaxCalculation amounts cannot "
            "be negative"
        )

    remaining_base = (
        calculated_base
    )

    remaining_tax = (
        calculated_tax
    )

    targets = []

    for candidate in sorted(
        eligible,
        key=_candidate_sort_key,
    ):
        if (
            remaining_base == ZERO
            and remaining_tax == ZERO
        ):
            break

        target_base = min(
            candidate
            .taxable_base_capacity,
            remaining_base,
        )

        target_tax = min(
            candidate
            .tax_amount_capacity,
            remaining_tax,
        )

        if (
            target_base == ZERO
            and target_tax == ZERO
        ):
            continue

        targets.append(
            TaxRecognitionSourceTarget(
                kind=candidate.kind,
                source_id=(
                    candidate.source_id
                ),
                event_date=(
                    candidate.event_date
                ),
                taxable_base=(
                    target_base
                ),
                tax_amount=(
                    target_tax
                ),
            )
        )

        remaining_base -= (
            target_base
        )

        remaining_tax -= (
            target_tax
        )

    total_base = sum(
        (
            target.taxable_base
            for target in targets
        ),
        ZERO,
    )

    total_tax = sum(
        (
            target.tax_amount
            for target in targets
        ),
        ZERO,
    )

    if (
        total_base > calculated_base
        or total_tax > calculated_tax
    ):
        raise TaxRecognitionDataIntegrityError(
            "Recognition target allocation "
            "exceeds TaxCalculation"
        )

    return tuple(
        targets
    )


def _target_map(
    targets: Iterable[
        TaxRecognitionSourceTarget
    ],
) -> dict[
    tuple[
        TaxRecognitionCandidateKind,
        int,
    ],
    TaxRecognitionSourceTarget,
]:
    result = {}

    for target in targets:
        if (
            target.source_key
            in result
        ):
            raise (
                DuplicateTaxRecognitionSourceError(
                    "Duplicate recognition "
                    "source target"
                )
            )

        result[
            target.source_key
        ] = target

    return result


def order_output_tax_reconciliations(
    *,
    current_targets: Iterable[
        TaxRecognitionSourceTarget
    ],
    desired_targets: Iterable[
        TaxRecognitionSourceTarget
    ],
) -> tuple[
    TaxRecognitionSourceTarget,
    ...,
]:
    """
    Produce safe reconciliation order.

    Sources that must shrink or disappear are reconciled
    before sources that must grow. This prevents a
    transient over-recognition when an earlier first event
    is reversed and a later still-active event takes over.
    """

    current = _target_map(
        current_targets
    )

    desired = _target_map(
        desired_targets
    )

    decreases = []
    increases = []

    all_keys = (
        set(current)
        | set(desired)
    )

    for key in all_keys:
        current_target = (
            current.get(
                key
            )
        )

        desired_target = (
            desired.get(
                key
            )
        )

        if current_target is None:
            if (
                desired_target is not None
                and not desired_target.is_zero
            ):
                increases.append(
                    desired_target
                )

            continue

        if desired_target is None:
            decreases.append(
                TaxRecognitionSourceTarget(
                    kind=(
                        current_target.kind
                    ),
                    source_id=(
                        current_target.source_id
                    ),
                    event_date=(
                        current_target.event_date
                    ),
                    taxable_base=ZERO,
                    tax_amount=ZERO,
                )
            )

            continue

        if (
            current_target.taxable_base
            == desired_target.taxable_base
            and current_target.tax_amount
            == desired_target.tax_amount
        ):
            continue

        base_decreased = (
            desired_target.taxable_base
            < current_target.taxable_base
        )

        tax_decreased = (
            desired_target.tax_amount
            < current_target.tax_amount
        )

        base_increased = (
            desired_target.taxable_base
            > current_target.taxable_base
        )

        tax_increased = (
            desired_target.tax_amount
            > current_target.tax_amount
        )

        if (
            (
                base_decreased
                and tax_increased
            )
            or (
                tax_decreased
                and base_increased
            )
        ):
            raise TaxRecognitionTargetStateError(
                "Recognition source cannot require "
                "a mixed increase/decrease adjustment"
            )

        if (
            base_decreased
            or tax_decreased
        ):
            decreases.append(
                desired_target
            )
        else:
            increases.append(
                desired_target
            )

    decreases.sort(
        key=_target_sort_key
    )

    increases.sort(
        key=_target_sort_key
    )

    return tuple(
        decreases
        + increases
    )
