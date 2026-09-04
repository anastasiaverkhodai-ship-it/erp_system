from dataclasses import dataclass
from datetime import date
from decimal import (
    Decimal,
    InvalidOperation,
    ROUND_HALF_UP,
)
from typing import Iterable


ZERO = Decimal("0.00")
MONEY_QUANTUM = Decimal("0.01")


class CustomerAdvanceClearingCalculationError(
    Exception
):
    """Base Customer Advance Clearing calculation error."""


class CustomerAdvanceClearingSourceError(
    CustomerAdvanceClearingCalculationError
):
    """A clearing source identity or date is invalid."""


class CustomerAdvanceClearingAmountError(
    CustomerAdvanceClearingCalculationError
):
    """A clearing monetary capacity is invalid."""


class CustomerAdvanceClearingCurrencyError(
    CustomerAdvanceClearingCalculationError
):
    """A clearing currency is invalid or inconsistent."""


@dataclass(
    frozen=True,
    slots=True,
)
class CustomerAdvanceSettlementCandidate:
    """
    Commercial RECEIVABLE settlement capacity.

    source_id identifies one ACTIVE
    PaymentSettlementAllocation.

    This capacity may exist before the related economic
    customer receivable has arisen in GL.
    """

    source_id: int
    event_date: date
    amount: Decimal
    currency_code: str


@dataclass(
    frozen=True,
    slots=True,
)
class CustomerEconomicReceivableCandidate:
    """
    Economic customer-receivable capacity.

    source_id identifies one ACTIVE SalesRecognitionEvent.

    amount is the tax-inclusive recognized_gross_amount
    represented by that immutable event.
    """

    source_id: int
    event_date: date
    amount: Decimal
    currency_code: str


@dataclass(
    frozen=True,
    slots=True,
)
class CustomerAdvanceClearingTarget:
    """
    Desired Dr customer-advances / Cr customer-receivables
    clearing tranche.

    settlement_source_id:
        PaymentSettlementAllocation.id

    receivable_source_id:
        SalesRecognitionEvent.id
    """

    settlement_source_id: int
    receivable_source_id: int
    event_date: date
    amount: Decimal
    currency_code: str


def money(
    amount: Decimal,
) -> Decimal:
    try:
        normalized = Decimal(
            str(amount)
        )
    except (
        InvalidOperation,
        TypeError,
        ValueError,
    ) as exc:
        raise CustomerAdvanceClearingAmountError(
            "Customer advance clearing amount "
            "must be a valid decimal"
        ) from exc

    if not normalized.is_finite():
        raise CustomerAdvanceClearingAmountError(
            "Customer advance clearing amount "
            "must be finite"
        )

    return normalized.quantize(
        MONEY_QUANTUM,
        rounding=ROUND_HALF_UP,
    )


def _positive_money(
    amount: Decimal,
) -> Decimal:
    normalized = money(
        amount
    )

    if normalized <= ZERO:
        raise CustomerAdvanceClearingAmountError(
            "Customer advance clearing capacity "
            "must be greater than zero"
        )

    return normalized


def _positive_source_id(
    source_id: int,
    *,
    label: str,
) -> int:
    if (
        isinstance(
            source_id,
            bool,
        )
        or not isinstance(
            source_id,
            int,
        )
        or source_id <= 0
    ):
        raise CustomerAdvanceClearingSourceError(
            f"{label} must be a positive integer"
        )

    return source_id


def _event_date(
    value: date,
    *,
    label: str,
) -> date:
    if not isinstance(
        value,
        date,
    ):
        raise CustomerAdvanceClearingSourceError(
            f"{label} must be a date"
        )

    return value


def _currency(
    value: str,
) -> str:
    if not isinstance(
        value,
        str,
    ):
        raise CustomerAdvanceClearingCurrencyError(
            "Customer advance clearing currency "
            "must be a string"
        )

    normalized = (
        value
        .strip()
        .upper()
    )

    if (
        len(
            normalized
        )
        != 3
        or not normalized.isalpha()
    ):
        raise CustomerAdvanceClearingCurrencyError(
            "Customer advance clearing currency "
            "must be a 3-letter code"
        )

    return normalized


def _normalize_settlements(
    candidates: Iterable[
        CustomerAdvanceSettlementCandidate
    ],
    *,
    currency_code: str,
) -> tuple[
    CustomerAdvanceSettlementCandidate,
    ...,
]:
    normalized = []
    seen_source_ids = set()

    for candidate in candidates:
        if not isinstance(
            candidate,
            CustomerAdvanceSettlementCandidate,
        ):
            raise CustomerAdvanceClearingSourceError(
                "Settlement candidate must be "
                "CustomerAdvanceSettlementCandidate"
            )

        source_id = _positive_source_id(
            candidate.source_id,
            label=(
                "Settlement source_id"
            ),
        )

        if source_id in seen_source_ids:
            raise CustomerAdvanceClearingSourceError(
                "Settlement source_id must be unique"
            )

        seen_source_ids.add(
            source_id
        )

        candidate_currency = _currency(
            candidate.currency_code
        )

        if (
            candidate_currency
            != currency_code
        ):
            raise CustomerAdvanceClearingCurrencyError(
                "Settlement candidate currency "
                "does not match clearing currency"
            )

        normalized.append(
            CustomerAdvanceSettlementCandidate(
                source_id=source_id,
                event_date=_event_date(
                    candidate.event_date,
                    label=(
                        "Settlement event_date"
                    ),
                ),
                amount=_positive_money(
                    candidate.amount
                ),
                currency_code=(
                    candidate_currency
                ),
            )
        )

    return tuple(
        sorted(
            normalized,
            key=lambda candidate: (
                candidate.event_date,
                candidate.source_id,
            ),
        )
    )


def _normalize_receivables(
    candidates: Iterable[
        CustomerEconomicReceivableCandidate
    ],
    *,
    currency_code: str,
) -> tuple[
    CustomerEconomicReceivableCandidate,
    ...,
]:
    normalized = []
    seen_source_ids = set()

    for candidate in candidates:
        if not isinstance(
            candidate,
            CustomerEconomicReceivableCandidate,
        ):
            raise CustomerAdvanceClearingSourceError(
                "Receivable candidate must be "
                "CustomerEconomicReceivableCandidate"
            )

        source_id = _positive_source_id(
            candidate.source_id,
            label=(
                "Receivable source_id"
            ),
        )

        if source_id in seen_source_ids:
            raise CustomerAdvanceClearingSourceError(
                "Receivable source_id must be unique"
            )

        seen_source_ids.add(
            source_id
        )

        candidate_currency = _currency(
            candidate.currency_code
        )

        if (
            candidate_currency
            != currency_code
        ):
            raise CustomerAdvanceClearingCurrencyError(
                "Receivable candidate currency "
                "does not match clearing currency"
            )

        normalized.append(
            CustomerEconomicReceivableCandidate(
                source_id=source_id,
                event_date=_event_date(
                    candidate.event_date,
                    label=(
                        "Receivable event_date"
                    ),
                ),
                amount=_positive_money(
                    candidate.amount
                ),
                currency_code=(
                    candidate_currency
                ),
            )
        )

    return tuple(
        sorted(
            normalized,
            key=lambda candidate: (
                candidate.event_date,
                candidate.source_id,
            ),
        )
    )


def build_customer_advance_clearing_targets(
    *,
    settlements: Iterable[
        CustomerAdvanceSettlementCandidate
    ],
    receivables: Iterable[
        CustomerEconomicReceivableCandidate
    ],
    currency_code: str,
) -> tuple[
    CustomerAdvanceClearingTarget,
    ...,
]:
    """
    Match commercial RECEIVABLE settlement capacity to
    economic customer-receivable capacity.

    Accounting meaning:

        incoming Payment:
            Dr 311
            Cr 681

        Sales recognition:
            Dr 361
            Cr 702
            (gross commercial amount)

        clearing, but only to the extent BOTH capacities exist:
            Dr 681
            Cr 361

    Matching rules:

    * settlement sources are FIFO by
      (event_date, source_id);
    * economic receivable sources are FIFO by
      (event_date, source_id);
    * one source may be split across multiple opposite sources;
    * total clearing cannot exceed either total capacity;
    * clearing date is max(
          settlement event date,
          economic receivable event date,
      ), because clearing cannot precede either source;
    * VAT recognition remains a separate tax/accounting layer.
    """
    currency = _currency(
        currency_code
    )

    settlement_candidates = (
        _normalize_settlements(
            settlements,
            currency_code=currency,
        )
    )

    receivable_candidates = (
        _normalize_receivables(
            receivables,
            currency_code=currency,
        )
    )

    if (
        not settlement_candidates
        or not receivable_candidates
    ):
        return ()

    targets = []

    settlement_index = 0
    receivable_index = 0

    settlement_remaining = (
        settlement_candidates[
            settlement_index
        ].amount
    )

    receivable_remaining = (
        receivable_candidates[
            receivable_index
        ].amount
    )

    while (
        settlement_index
        < len(
            settlement_candidates
        )
        and receivable_index
        < len(
            receivable_candidates
        )
    ):
        settlement = (
            settlement_candidates[
                settlement_index
            ]
        )

        receivable = (
            receivable_candidates[
                receivable_index
            ]
        )

        tranche = min(
            settlement_remaining,
            receivable_remaining,
        )

        if tranche <= ZERO:
            raise CustomerAdvanceClearingAmountError(
                "Calculated customer advance clearing "
                "tranche must be greater than zero"
            )

        targets.append(
            CustomerAdvanceClearingTarget(
                settlement_source_id=(
                    settlement.source_id
                ),
                receivable_source_id=(
                    receivable.source_id
                ),
                event_date=max(
                    settlement.event_date,
                    receivable.event_date,
                ),
                amount=money(
                    tranche
                ),
                currency_code=currency,
            )
        )

        settlement_remaining = money(
            settlement_remaining
            - tranche
        )

        receivable_remaining = money(
            receivable_remaining
            - tranche
        )

        if (
            settlement_remaining
            == ZERO
        ):
            settlement_index += 1

            if (
                settlement_index
                < len(
                    settlement_candidates
                )
            ):
                settlement_remaining = (
                    settlement_candidates[
                        settlement_index
                    ].amount
                )

        if (
            receivable_remaining
            == ZERO
        ):
            receivable_index += 1

            if (
                receivable_index
                < len(
                    receivable_candidates
                )
            ):
                receivable_remaining = (
                    receivable_candidates[
                        receivable_index
                    ].amount
                )

    return tuple(
        targets
    )
