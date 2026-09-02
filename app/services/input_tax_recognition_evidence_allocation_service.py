from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Iterable

from app.services.input_tax_recognition_calculation_service import (
    InputTaxRecognitionDataIntegrityError,
    calculate_input_tax_recognition_limit,
)
from app.services.tax_credit_evidence_persistence_service import (
    TaxCreditEvidenceDataIntegrityError,
    TaxCreditEvidenceWindow,
    build_tax_credit_evidence_windows,
)
from app.services.tax_recognition_orchestration_service import (
    TaxRecognitionCandidate,
    TaxRecognitionCandidateKind,
)
from app.services.tax_recognition_types import (
    TaxRecognitionMethod,
)


ZERO = Decimal("0")


class InputTaxRecognitionEvidenceAllocationError(
    Exception
):
    """Base INPUT VAT evidence-allocation error."""


class InputTaxRecognitionEvidenceAllocationStateError(
    InputTaxRecognitionEvidenceAllocationError
):
    """Evidence allocation state is inconsistent."""


@dataclass(
    frozen=True,
    slots=True,
)
class InputTaxRecognitionEvidenceTarget:
    """
    Desired current INPUT VAT recognition net for one
    immutable TaxCreditEvidence original row.

    event_date is the earliest date on which BOTH:
    - enough economic capacity existed; and
    - this evidence was active

    for the target amount represented by this source.

    Later reconciliation may increase the same evidence source
    with another immutable TaxRecognitionEvent dated at the
    later economic/evidence threshold.
    """

    tax_credit_evidence_id: int
    event_date: date
    taxable_base: Decimal
    tax_amount: Decimal

    def __post_init__(
        self,
    ) -> None:
        if (
            self.tax_credit_evidence_id
            <= 0
        ):
            raise (
                InputTaxRecognitionEvidenceAllocationStateError(
                    "TaxCreditEvidence source ID "
                    "must be greater than zero"
                )
            )

        if (
            self.taxable_base < ZERO
            or self.tax_amount < ZERO
        ):
            raise (
                InputTaxRecognitionEvidenceAllocationStateError(
                    "INPUT recognition evidence target "
                    "cannot be negative"
                )
            )

        if (
            self.taxable_base == ZERO
            and self.tax_amount == ZERO
        ):
            raise (
                InputTaxRecognitionEvidenceAllocationStateError(
                    "Positive evidence target cannot "
                    "contain two zero amounts"
                )
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
            InputTaxRecognitionEvidenceAllocationStateError(
                f"{field} must be a decimal amount"
            )
        ) from exc

    if not result.is_finite():
        raise (
            InputTaxRecognitionEvidenceAllocationStateError(
                f"{field} must be finite"
            )
        )

    return result


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

    raise (
        InputTaxRecognitionEvidenceAllocationStateError(
            "Unsupported economic candidate kind"
        )
    )


def _eligible_economic_candidates(
    *,
    calculation,
    candidates: tuple[
        TaxRecognitionCandidate,
        ...,
    ],
    as_of_date: date,
) -> tuple[
    TaxRecognitionCandidate,
    ...,
]:
    try:
        method = TaxRecognitionMethod(
            calculation.recognition_method
        )
    except ValueError as exc:
        raise (
            InputTaxRecognitionEvidenceAllocationStateError(
                "Unsupported recognition method"
            )
        ) from exc

    result = []

    for candidate in candidates:
        if (
            candidate.event_date
            > as_of_date
        ):
            continue

        if (
            method
            == TaxRecognitionMethod.CASH_METHOD
            and candidate.kind
            != TaxRecognitionCandidateKind.SETTLEMENT
        ):
            continue

        result.append(
            candidate
        )

    return tuple(
        sorted(
            result,
            key=lambda item: (
                item.event_date,
                _candidate_kind_order(
                    item.kind
                ),
                item.source_id,
            ),
        )
    )


def _economic_threshold_date(
    *,
    candidates: tuple[
        TaxRecognitionCandidate,
        ...,
    ],
    required_base: Decimal,
    required_tax: Decimal,
    calculated_base: Decimal,
    calculated_tax: Decimal,
) -> date | None:
    """
    Return the first economic-event date by which cumulative
    eligible economic capacity reaches BOTH requested thresholds.

    Capacity is capped by the immutable TaxCalculation snapshot.
    """

    if (
        required_base < ZERO
        or required_tax < ZERO
    ):
        raise (
            InputTaxRecognitionEvidenceAllocationStateError(
                "Economic threshold cannot be negative"
            )
        )

    if (
        required_base == ZERO
        and required_tax == ZERO
    ):
        return None

    cumulative_base = ZERO
    cumulative_tax = ZERO

    for candidate in candidates:
        capacity_base = _decimal(
            candidate.taxable_base_capacity,
            field=(
                "Economic candidate "
                "taxable_base_capacity"
            ),
        )

        capacity_tax = _decimal(
            candidate.tax_amount_capacity,
            field=(
                "Economic candidate "
                "tax_amount_capacity"
            ),
        )

        if (
            capacity_base < ZERO
            or capacity_tax < ZERO
        ):
            raise (
                InputTaxRecognitionEvidenceAllocationStateError(
                    "Economic candidate capacity "
                    "cannot be negative"
                )
            )

        remaining_base = max(
            calculated_base
            - cumulative_base,
            ZERO,
        )

        remaining_tax = max(
            calculated_tax
            - cumulative_tax,
            ZERO,
        )

        cumulative_base += min(
            capacity_base,
            remaining_base,
        )

        cumulative_tax += min(
            capacity_tax,
            remaining_tax,
        )

        if (
            cumulative_base
            >= required_base
            and cumulative_tax
            >= required_tax
        ):
            return candidate.event_date

    raise (
        InputTaxRecognitionEvidenceAllocationStateError(
            "Recognizable INPUT VAT exceeds "
            "available economic timeline capacity"
        )
    )


def _active_windows(
    *,
    evidence_events: tuple,
    as_of_date: date,
) -> tuple[
    TaxCreditEvidenceWindow,
    ...,
]:
    try:
        windows = (
            build_tax_credit_evidence_windows(
                events=evidence_events
            )
        )
    except TaxCreditEvidenceDataIntegrityError as exc:
        raise (
            InputTaxRecognitionEvidenceAllocationStateError(
                str(exc)
            )
        ) from exc

    return tuple(
        sorted(
            (
                window
                for window in windows
                if (
                    window.start_date
                    <= as_of_date
                    and (
                        window.end_date
                        is None
                        or as_of_date
                        < window.end_date
                    )
                )
            ),
            key=lambda item: (
                item.start_date,
                item.event_id,
            ),
        )
    )


def build_input_tax_recognition_evidence_targets(
    *,
    calculation,
    economic_candidates: Iterable[
        TaxRecognitionCandidate
    ],
    evidence_events: Iterable,
    as_of_date: date,
) -> tuple[
    InputTaxRecognitionEvidenceTarget,
    ...,
]:
    """
    Allocate currently recognizable INPUT VAT into immutable
    temporal evidence tranches.

    One TaxCreditEvidence source may produce multiple targets
    when economic capacity becomes available on different dates.

    Example:

        evidence 20 available D1
        economic 10 available D2
        economic 10 available D3

    becomes:

        evidence_id=X, 10 @ D2
        evidence_id=X, 10 @ D3

    This preserves the real tax-recognition timeline and avoids
    moving an already-recognized earlier tranche to a later date.

    Allocation is deterministic:

        evidence FIFO:
            evidence effective date, evidence id

        economic FIFO:
            event date, candidate kind, source id

    No database writes are performed here.
    """

    candidate_tuple = tuple(
        economic_candidates
    )

    evidence_tuple = tuple(
        evidence_events
    )

    limit = (
        calculate_input_tax_recognition_limit(
            calculation=calculation,
            economic_candidates=(
                candidate_tuple
            ),
            evidence_events=(
                evidence_tuple
            ),
            as_of_date=as_of_date,
        )
    )

    if limit.is_zero:
        return ()

    calculated_base = _decimal(
        calculation.taxable_base,
        field="TaxCalculation taxable_base",
    )

    calculated_tax = _decimal(
        calculation.tax_amount,
        field="TaxCalculation tax_amount",
    )

    active_windows = _active_windows(
        evidence_events=evidence_tuple,
        as_of_date=as_of_date,
    )

    if not active_windows:
        raise (
            InputTaxRecognitionEvidenceAllocationStateError(
                "Recognizable INPUT VAT exists "
                "without ACTIVE TaxCreditEvidence"
            )
        )

    eligible_economic = (
        _eligible_economic_candidates(
            calculation=calculation,
            candidates=candidate_tuple,
            as_of_date=as_of_date,
        )
    )

    if not eligible_economic:
        raise (
            InputTaxRecognitionEvidenceAllocationStateError(
                "Recognizable INPUT VAT exists "
                "without eligible economic capacity"
            )
        )

    #
    # Build economic temporal segments.
    #
    # Candidate capacity is capped by the immutable
    # TaxCalculation exactly once across the whole timeline.
    #
    economic_segments = []

    calculation_remaining_base = (
        calculated_base
    )

    calculation_remaining_tax = (
        calculated_tax
    )

    for candidate in eligible_economic:
        if (
            calculation_remaining_base
            == ZERO
            and calculation_remaining_tax
            == ZERO
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
            raise (
                InputTaxRecognitionEvidenceAllocationStateError(
                    "Economic candidate capacity "
                    "cannot be negative"
                )
            )

        segment_base = min(
            candidate_base,
            calculation_remaining_base,
        )

        segment_tax = min(
            candidate_tax,
            calculation_remaining_tax,
        )

        if (
            segment_base == ZERO
            and segment_tax == ZERO
        ):
            continue

        economic_segments.append(
            {
                "event_date": (
                    candidate.event_date
                ),
                "base": segment_base,
                "tax": segment_tax,
            }
        )

        calculation_remaining_base -= (
            segment_base
        )

        calculation_remaining_tax -= (
            segment_tax
        )

    remaining_target_base = (
        limit.recognizable_taxable_base
    )

    remaining_target_tax = (
        limit.recognizable_tax_amount
    )

    economic_index = 0

    raw_targets = []

    for window in active_windows:
        if (
            remaining_target_base == ZERO
            and remaining_target_tax == ZERO
        ):
            break

        evidence_remaining_base = min(
            window.evidenced_taxable_base,
            remaining_target_base,
        )

        evidence_remaining_tax = min(
            window.evidenced_tax_amount,
            remaining_target_tax,
        )

        while (
            (
                evidence_remaining_base
                > ZERO
                or evidence_remaining_tax
                > ZERO
            )
            and (
                remaining_target_base
                > ZERO
                or remaining_target_tax
                > ZERO
            )
        ):
            while (
                economic_index
                < len(economic_segments)
                and economic_segments[
                    economic_index
                ]["base"] == ZERO
                and economic_segments[
                    economic_index
                ]["tax"] == ZERO
            ):
                economic_index += 1

            if (
                economic_index
                >= len(economic_segments)
            ):
                raise (
                    InputTaxRecognitionEvidenceAllocationStateError(
                        "Economic timeline capacity ended "
                        "before recognizable INPUT VAT "
                        "was fully allocated"
                    )
                )

            economic = (
                economic_segments[
                    economic_index
                ]
            )

            allocated_base = min(
                evidence_remaining_base,
                economic["base"],
                remaining_target_base,
            )

            allocated_tax = min(
                evidence_remaining_tax,
                economic["tax"],
                remaining_target_tax,
            )

            if (
                allocated_base == ZERO
                and allocated_tax == ZERO
            ):
                raise (
                    InputTaxRecognitionEvidenceAllocationStateError(
                        "Taxable-base and tax capacities "
                        "cannot be aligned into an "
                        "INPUT recognition tranche"
                    )
                )

            event_date = max(
                window.start_date,
                economic["event_date"],
            )

            raw_targets.append(
                InputTaxRecognitionEvidenceTarget(
                    tax_credit_evidence_id=(
                        window.event_id
                    ),
                    event_date=event_date,
                    taxable_base=(
                        allocated_base
                    ),
                    tax_amount=(
                        allocated_tax
                    ),
                )
            )

            evidence_remaining_base -= (
                allocated_base
            )

            evidence_remaining_tax -= (
                allocated_tax
            )

            economic["base"] -= (
                allocated_base
            )

            economic["tax"] -= (
                allocated_tax
            )

            remaining_target_base -= (
                allocated_base
            )

            remaining_target_tax -= (
                allocated_tax
            )

            if (
                economic["base"] == ZERO
                and economic["tax"] == ZERO
            ):
                economic_index += 1

    if (
        remaining_target_base != ZERO
        or remaining_target_tax != ZERO
    ):
        raise (
            InputTaxRecognitionEvidenceAllocationStateError(
                "ACTIVE evidence/economic allocation "
                "did not cover recognizable INPUT VAT"
            )
        )

    #
    # Consolidate adjacent candidates that resolve to the same
    # evidence source and the same recognition date.
    #
    consolidated = {}

    for target in raw_targets:
        key = (
            target.tax_credit_evidence_id,
            target.event_date,
        )

        current = consolidated.get(
            key
        )

        if current is None:
            consolidated[
                key
            ] = [
                target.taxable_base,
                target.tax_amount,
            ]
        else:
            current[0] += (
                target.taxable_base
            )

            current[1] += (
                target.tax_amount
            )

    targets = tuple(
        InputTaxRecognitionEvidenceTarget(
            tax_credit_evidence_id=source_id,
            event_date=event_date,
            taxable_base=amounts[0],
            tax_amount=amounts[1],
        )
        for (
            source_id,
            event_date,
        ), amounts in sorted(
            consolidated.items(),
            key=lambda item: (
                item[0][1],
                item[0][0],
            ),
        )
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
        total_base
        != limit.recognizable_taxable_base
        or total_tax
        != limit.recognizable_tax_amount
    ):
        raise (
            InputTaxRecognitionEvidenceAllocationStateError(
                "Temporal evidence target totals "
                "differ from INPUT recognition limit"
            )
        )

    return targets
