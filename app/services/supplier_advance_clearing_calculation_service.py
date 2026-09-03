from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.services.money_rounding import (
    round_currency_amount,
)


ZERO = Decimal("0.00")


class SupplierAdvanceClearingCalculationError(
    Exception
):
    """Base supplier-advance clearing calculation error."""


class SupplierAdvanceClearingSourceError(
    SupplierAdvanceClearingCalculationError
):
    """Source identity or chronology is invalid."""


class SupplierAdvanceClearingAmountError(
    SupplierAdvanceClearingCalculationError
):
    """Supplier-advance clearing amount is invalid."""


class SupplierAdvanceClearingCurrencyError(
    SupplierAdvanceClearingCalculationError
):
    """Currency code is invalid."""


@dataclass(
    frozen=True,
    slots=True,
)
class SupplierAdvanceSettlementCandidate:
    """
    Commercial settlement capacity.

    A confirmed outgoing Payment may already be commercially
    allocated to a PAYABLE Open Item even when the related
    economic supplier liability has not yet arisen in GL.
    """

    source_id: int
    event_date: date
    amount: Decimal


@dataclass(
    frozen=True,
    slots=True,
)
class SupplierEconomicLiabilityCandidate:
    """
    Economic supplier-liability capacity.

    A candidate represents liability that has actually arisen
    from the purchase/receipt accounting layer and is therefore
    available for clearing against supplier advances.
    """

    source_id: int
    event_date: date
    amount: Decimal


@dataclass(
    frozen=True,
    slots=True,
)
class SupplierAdvanceClearingTarget:
    """
    Desired Dr supplier-payables / Cr supplier-advances tranche.

    event_date is never earlier than either underlying source.
    """

    settlement_source_id: int
    liability_source_id: int
    event_date: date
    amount: Decimal
    currency_code: str


def _validate_currency_code(
    currency_code: str,
) -> str:
    if not isinstance(
        currency_code,
        str,
    ):
        raise SupplierAdvanceClearingCurrencyError(
            "currency_code must be a string"
        )

    normalized = (
        currency_code
        .strip()
        .upper()
    )

    if (
        len(normalized) != 3
        or not normalized.isalpha()
    ):
        raise SupplierAdvanceClearingCurrencyError(
            "currency_code must contain "
            "exactly three letters"
        )

    return normalized


def _validate_source_id(
    source_id: int,
    *,
    label: str,
) -> None:
    if (
        not isinstance(
            source_id,
            int,
        )
        or isinstance(
            source_id,
            bool,
        )
        or source_id <= 0
    ):
        raise SupplierAdvanceClearingSourceError(
            f"{label} source_id must be "
            "a positive integer"
        )


def _validate_event_date(
    event_date: date,
    *,
    label: str,
) -> None:
    if not isinstance(
        event_date,
        date,
    ):
        raise SupplierAdvanceClearingSourceError(
            f"{label} event_date must be a date"
        )


def _positive_money(
    value: Decimal,
    *,
    currency_code: str,
    label: str,
) -> Decimal:
    try:
        amount = round_currency_amount(
            amount=Decimal(
                str(value)
            ),
            currency_code=currency_code,
        )
    except Exception as exc:
        raise SupplierAdvanceClearingAmountError(
            f"{label} amount is invalid"
        ) from exc

    if amount <= ZERO:
        raise SupplierAdvanceClearingAmountError(
            f"{label} amount must be "
            "greater than zero"
        )

    return amount


def _validate_unique_source_ids(
    source_ids: tuple[int, ...],
    *,
    label: str,
) -> None:
    if len(
        source_ids
    ) != len(
        set(
            source_ids
        )
    ):
        raise SupplierAdvanceClearingSourceError(
            f"{label} source_id values "
            "must be unique"
        )


def build_supplier_advance_clearing_targets(
    *,
    settlements: tuple[
        SupplierAdvanceSettlementCandidate,
        ...,
    ],
    liabilities: tuple[
        SupplierEconomicLiabilityCandidate,
        ...,
    ],
    currency_code: str,
) -> tuple[
    SupplierAdvanceClearingTarget,
    ...,
]:
    """
    Match commercial supplier settlements to economic liability.

    Accounting rule:

        clearable amount
            = min(
                commercial settlement capacity,
                economic supplier-liability capacity,
              )

    Matching is deterministic FIFO by:

        (event_date, source_id)

    A target's accounting date is:

        max(
            settlement.event_date,
            liability.event_date,
        )

    Therefore:

    * payment/settlement first:
        no Dr631/Cr371 exists until economic liability arrives;

    * receipt first:
        clearing occurs when settlement later arrives;

    * partial receipt:
        one settlement may be cleared in multiple later tranches;

    * partial settlement:
        one liability source may clear multiple settlements.

    This service is pure. It performs no persistence, posting,
    transaction management, or database access.
    """

    currency = _validate_currency_code(
        currency_code
    )

    settlement_ids = tuple(
        candidate.source_id
        for candidate in settlements
    )

    liability_ids = tuple(
        candidate.source_id
        for candidate in liabilities
    )

    _validate_unique_source_ids(
        settlement_ids,
        label="Settlement",
    )

    _validate_unique_source_ids(
        liability_ids,
        label="Liability",
    )

    normalized_settlements: list[
        tuple[
            SupplierAdvanceSettlementCandidate,
            Decimal,
        ]
    ] = []

    for candidate in settlements:
        _validate_source_id(
            candidate.source_id,
            label="Settlement",
        )

        _validate_event_date(
            candidate.event_date,
            label="Settlement",
        )

        normalized_settlements.append(
            (
                candidate,
                _positive_money(
                    candidate.amount,
                    currency_code=currency,
                    label="Settlement",
                ),
            )
        )

    normalized_liabilities: list[
        tuple[
            SupplierEconomicLiabilityCandidate,
            Decimal,
        ]
    ] = []

    for candidate in liabilities:
        _validate_source_id(
            candidate.source_id,
            label="Liability",
        )

        _validate_event_date(
            candidate.event_date,
            label="Liability",
        )

        normalized_liabilities.append(
            (
                candidate,
                _positive_money(
                    candidate.amount,
                    currency_code=currency,
                    label="Liability",
                ),
            )
        )

    normalized_settlements.sort(
        key=lambda item: (
            item[0].event_date,
            item[0].source_id,
        )
    )

    normalized_liabilities.sort(
        key=lambda item: (
            item[0].event_date,
            item[0].source_id,
        )
    )

    if (
        not normalized_settlements
        or not normalized_liabilities
    ):
        return ()

    settlement_index = 0
    liability_index = 0

    settlement_remaining = (
        normalized_settlements[
            settlement_index
        ][1]
    )

    liability_remaining = (
        normalized_liabilities[
            liability_index
        ][1]
    )

    targets: list[
        SupplierAdvanceClearingTarget
    ] = []

    while (
        settlement_index
        < len(
            normalized_settlements
        )
        and liability_index
        < len(
            normalized_liabilities
        )
    ):
        settlement = (
            normalized_settlements[
                settlement_index
            ][0]
        )

        liability = (
            normalized_liabilities[
                liability_index
            ][0]
        )

        matched_amount = min(
            settlement_remaining,
            liability_remaining,
        )

        if matched_amount <= ZERO:
            raise SupplierAdvanceClearingAmountError(
                "Internal matched amount must "
                "be greater than zero"
            )

        targets.append(
            SupplierAdvanceClearingTarget(
                settlement_source_id=(
                    settlement.source_id
                ),
                liability_source_id=(
                    liability.source_id
                ),
                event_date=max(
                    settlement.event_date,
                    liability.event_date,
                ),
                amount=matched_amount,
                currency_code=currency,
            )
        )

        settlement_remaining -= (
            matched_amount
        )

        liability_remaining -= (
            matched_amount
        )

        if settlement_remaining == ZERO:
            settlement_index += 1

            if (
                settlement_index
                < len(
                    normalized_settlements
                )
            ):
                settlement_remaining = (
                    normalized_settlements[
                        settlement_index
                    ][1]
                )

        if liability_remaining == ZERO:
            liability_index += 1

            if (
                liability_index
                < len(
                    normalized_liabilities
                )
            ):
                liability_remaining = (
                    normalized_liabilities[
                        liability_index
                    ][1]
                )

    return tuple(
        targets
    )
