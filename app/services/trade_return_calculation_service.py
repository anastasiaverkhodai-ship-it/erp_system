from dataclasses import dataclass
from datetime import date
from decimal import (
    Decimal,
    InvalidOperation,
)
from typing import Iterable

from app.services.money_rounding import (
    round_currency_amount,
)


ZERO = Decimal("0")


class TradeReturnCalculationError(Exception):
    """Base pure-math return calculation error."""


class TradeReturnDataIntegrityError(
    TradeReturnCalculationError
):
    """Return source data is internally inconsistent."""


class TradeReturnCapacityError(
    TradeReturnCalculationError
):
    """Requested returned quantity exceeds economic capacity."""


class TradeReturnChronologyError(
    TradeReturnCalculationError
):
    """A return attempts to consume future economic capacity."""


class TradeValueCorrectionError(
    TradeReturnCalculationError
):
    """Commercial before/after correction state is invalid."""


@dataclass(
    frozen=True,
    slots=True,
)
class TradeReturnEconomicCapacity:
    """
    One immutable economic fulfillment capacity.

    The service deliberately does not know whether the source is
    Sales or Purchase. Loaders provide the correct economic source.

    For Sales this can be built from immutable SalesRecognition
    state. Purchase will later provide its own economic liability
    source.

    tax_amount is only the commercial/tax snapshot component
    belonging to this economic amount. It does NOT itself authorize
    or post any VAT adjustment.
    """

    source_id: int
    event_date: date
    quantity: Decimal
    gross_amount: Decimal
    tax_amount: Decimal
    currency_code: str


@dataclass(
    frozen=True,
    slots=True,
)
class TradeReturnCandidate:
    """
    One immutable positive physical/commercial return fact.

    source_id is the future immutable return-source identity,
    normally derived from a dedicated Return document line/event.
    """

    source_id: int
    event_date: date
    quantity: Decimal


@dataclass(
    frozen=True,
    slots=True,
)
class TradeReturnTarget:
    """
    Desired allocation of one return source against one original
    economic fulfillment source.

    The pair is immutable provenance:

        return_source_id
        +
        economic_source_id

    One return source may span multiple economic sources.
    """

    return_source_id: int
    economic_source_id: int
    event_date: date
    quantity: Decimal
    gross_amount: Decimal
    tax_amount: Decimal
    currency_code: str

    @property
    def pair_key(
        self,
    ) -> tuple[int, int]:
        return (
            self.return_source_id,
            self.economic_source_id,
        )

    @property
    def taxable_base_amount(
        self,
    ) -> Decimal:
        return (
            self.gross_amount
            - self.tax_amount
        )


@dataclass(
    frozen=True,
    slots=True,
)
class TradeValueCorrectionTarget:
    """
    Pure commercial before/after amount correction.

    Deltas are signed:

        negative -> compensation decreases
        positive -> compensation increases
        zero     -> no commercial amount change

    tax_amount_delta is only the commercial tax component delta.
    Legal VAT recognition will later be controlled by the separate
    Ukrainian RK / tax-correction lifecycle.
    """

    original_gross_amount: Decimal
    original_tax_amount: Decimal
    corrected_gross_amount: Decimal
    corrected_tax_amount: Decimal
    gross_amount_delta: Decimal
    tax_amount_delta: Decimal
    taxable_base_delta: Decimal
    currency_code: str

    @property
    def is_noop(
        self,
    ) -> bool:
        return (
            self.gross_amount_delta
            == ZERO
            and self.tax_amount_delta
            == ZERO
        )


@dataclass(
    slots=True,
)
class _CapacityState:
    source: TradeReturnEconomicCapacity
    returned_quantity: Decimal = ZERO
    returned_gross_amount: Decimal = ZERO
    returned_tax_amount: Decimal = ZERO

    @property
    def available_quantity(
        self,
    ) -> Decimal:
        return (
            self.source.quantity
            - self.returned_quantity
        )


def _decimal(
    value: Decimal,
    *,
    field: str,
) -> Decimal:
    try:
        result = (
            value
            if isinstance(
                value,
                Decimal,
            )
            else Decimal(
                str(
                    value
                )
            )
        )
    except (
        InvalidOperation,
        TypeError,
        ValueError,
    ) as exc:
        raise TradeReturnDataIntegrityError(
            f"{field} must be a finite Decimal"
        ) from exc

    if not result.is_finite():
        raise TradeReturnDataIntegrityError(
            f"{field} must be finite"
        )

    return result


def _currency(
    value: str,
) -> str:
    normalized = str(
        value
    ).strip().upper()

    if (
        len(
            normalized
        )
        != 3
        or not normalized.isalpha()
    ):
        raise TradeReturnDataIntegrityError(
            "currency_code must contain exactly "
            "three alphabetic characters"
        )

    return normalized


def _business_date(
    value: date,
    *,
    field: str,
) -> date:
    if not isinstance(
        value,
        date,
    ):
        raise TradeReturnDataIntegrityError(
            f"{field} must be a date"
        )

    return value


def _positive_id(
    value: int,
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
        raise TradeReturnDataIntegrityError(
            f"{field} must be greater than zero"
        )

    return value


def _money(
    value: Decimal,
    *,
    currency_code: str,
    field: str,
) -> Decimal:
    amount = _decimal(
        value,
        field=field,
    )

    return round_currency_amount(
        amount=amount,
        currency_code=currency_code,
    )


def _validate_capacity(
    value: TradeReturnEconomicCapacity,
    *,
    expected_currency: str,
) -> TradeReturnEconomicCapacity:
    if not isinstance(
        value,
        TradeReturnEconomicCapacity,
    ):
        raise TradeReturnDataIntegrityError(
            "capacities must contain "
            "TradeReturnEconomicCapacity"
        )

    source_id = _positive_id(
        value.source_id,
        field="economic source_id",
    )

    event_date = _business_date(
        value.event_date,
        field="economic event_date",
    )

    quantity = _decimal(
        value.quantity,
        field="economic quantity",
    )

    if quantity <= ZERO:
        raise TradeReturnDataIntegrityError(
            "economic quantity must be greater than zero"
        )

    currency_code = _currency(
        value.currency_code
    )

    if (
        currency_code
        != expected_currency
    ):
        raise TradeReturnDataIntegrityError(
            "economic source currency mismatch"
        )

    gross_amount = _money(
        value.gross_amount,
        currency_code=currency_code,
        field="economic gross_amount",
    )

    tax_amount = _money(
        value.tax_amount,
        currency_code=currency_code,
        field="economic tax_amount",
    )

    if gross_amount <= ZERO:
        raise TradeReturnDataIntegrityError(
            "economic gross_amount must be greater than zero"
        )

    if tax_amount < ZERO:
        raise TradeReturnDataIntegrityError(
            "economic tax_amount cannot be negative"
        )

    if tax_amount > gross_amount:
        raise TradeReturnDataIntegrityError(
            "economic tax_amount cannot exceed gross_amount"
        )

    return TradeReturnEconomicCapacity(
        source_id=source_id,
        event_date=event_date,
        quantity=quantity,
        gross_amount=gross_amount,
        tax_amount=tax_amount,
        currency_code=currency_code,
    )


def _validate_candidate(
    value: TradeReturnCandidate,
) -> TradeReturnCandidate:
    if not isinstance(
        value,
        TradeReturnCandidate,
    ):
        raise TradeReturnDataIntegrityError(
            "candidates must contain TradeReturnCandidate"
        )

    source_id = _positive_id(
        value.source_id,
        field="return source_id",
    )

    event_date = _business_date(
        value.event_date,
        field="return event_date",
    )

    quantity = _decimal(
        value.quantity,
        field="return quantity",
    )

    if quantity <= ZERO:
        raise TradeReturnDataIntegrityError(
            "return quantity must be greater than zero"
        )

    return TradeReturnCandidate(
        source_id=source_id,
        event_date=event_date,
        quantity=quantity,
    )


def _validated_capacities(
    *,
    capacities: Iterable[
        TradeReturnEconomicCapacity
    ],
    currency_code: str,
) -> tuple[
    TradeReturnEconomicCapacity,
    ...,
]:
    normalized = []

    seen_ids = set()

    for raw in capacities:
        item = _validate_capacity(
            raw,
            expected_currency=currency_code,
        )

        if item.source_id in seen_ids:
            raise TradeReturnDataIntegrityError(
                "duplicate economic source_id"
            )

        seen_ids.add(
            item.source_id
        )

        normalized.append(
            item
        )

    return tuple(
        sorted(
            normalized,
            key=lambda item: (
                item.event_date,
                item.source_id,
            ),
        )
    )


def _validated_candidates(
    candidates: Iterable[
        TradeReturnCandidate
    ],
) -> tuple[
    TradeReturnCandidate,
    ...,
]:
    normalized = []

    seen_ids = set()

    for raw in candidates:
        item = _validate_candidate(
            raw
        )

        if item.source_id in seen_ids:
            raise TradeReturnDataIntegrityError(
                "duplicate return source_id"
            )

        seen_ids.add(
            item.source_id
        )

        normalized.append(
            item
        )

    return tuple(
        sorted(
            normalized,
            key=lambda item: (
                item.event_date,
                item.source_id,
            ),
        )
    )


def _cumulative_amount(
    *,
    total_amount: Decimal,
    total_quantity: Decimal,
    cumulative_quantity: Decimal,
    currency_code: str,
) -> Decimal:
    if cumulative_quantity < ZERO:
        raise TradeReturnDataIntegrityError(
            "cumulative quantity cannot be negative"
        )

    if cumulative_quantity > total_quantity:
        raise TradeReturnCapacityError(
            "cumulative returned quantity exceeds "
            "economic quantity"
        )

    if cumulative_quantity == ZERO:
        return ZERO

    if cumulative_quantity == total_quantity:
        return total_amount

    return round_currency_amount(
        amount=(
            total_amount
            * cumulative_quantity
            / total_quantity
        ),
        currency_code=currency_code,
    )


def _allocate_from_capacity(
    *,
    state: _CapacityState,
    quantity: Decimal,
    return_source_id: int,
    return_date: date,
    currency_code: str,
) -> TradeReturnTarget:
    if quantity <= ZERO:
        raise TradeReturnDataIntegrityError(
            "allocated return quantity "
            "must be greater than zero"
        )

    if (
        quantity
        > state.available_quantity
    ):
        raise TradeReturnCapacityError(
            "allocated return quantity exceeds "
            "economic source capacity"
        )

    cumulative_quantity = (
        state.returned_quantity
        + quantity
    )

    cumulative_gross = (
        _cumulative_amount(
            total_amount=(
                state.source.gross_amount
            ),
            total_quantity=(
                state.source.quantity
            ),
            cumulative_quantity=(
                cumulative_quantity
            ),
            currency_code=currency_code,
        )
    )

    cumulative_tax = (
        _cumulative_amount(
            total_amount=(
                state.source.tax_amount
            ),
            total_quantity=(
                state.source.quantity
            ),
            cumulative_quantity=(
                cumulative_quantity
            ),
            currency_code=currency_code,
        )
    )

    gross_slice = (
        cumulative_gross
        - state.returned_gross_amount
    )

    tax_slice = (
        cumulative_tax
        - state.returned_tax_amount
    )

    if gross_slice < ZERO:
        raise TradeReturnDataIntegrityError(
            "allocated gross return amount "
            "cannot be negative"
        )

    if tax_slice < ZERO:
        raise TradeReturnDataIntegrityError(
            "allocated tax return amount "
            "cannot be negative"
        )

    if tax_slice > gross_slice:
        raise TradeReturnDataIntegrityError(
            "allocated tax return amount "
            "cannot exceed gross amount"
        )

    state.returned_quantity = (
        cumulative_quantity
    )

    state.returned_gross_amount = (
        cumulative_gross
    )

    state.returned_tax_amount = (
        cumulative_tax
    )

    return TradeReturnTarget(
        return_source_id=return_source_id,
        economic_source_id=(
            state.source.source_id
        ),
        event_date=return_date,
        quantity=quantity,
        gross_amount=gross_slice,
        tax_amount=tax_slice,
        currency_code=currency_code,
    )


def build_trade_return_targets(
    *,
    capacities: Iterable[
        TradeReturnEconomicCapacity
    ],
    candidates: Iterable[
        TradeReturnCandidate
    ],
    currency_code: str,
) -> tuple[
    TradeReturnTarget,
    ...,
]:
    """
    Allocate immutable positive Return facts against immutable
    economic fulfillment capacity.

    Allocation rules:

    - economic capacity FIFO:
          event_date, source_id
    - return facts FIFO:
          event_date, source_id
    - one return source may split across economic sources;
    - a return cannot consume economic capacity dated after the
      return fact;
    - over-return is rejected, never silently capped;
    - monetary slices use cumulative currency rounding so a full
      return reproduces the exact immutable source totals.

    This is pure economic/commercial math only.

    It does not:
    - post JournalEntry;
    - restore stock or COGS;
    - change AR/AP;
    - recognize or reverse Ukrainian VAT;
    - create an RK;
    - write management registers.
    """

    normalized_currency = _currency(
        currency_code
    )

    ordered_capacities = (
        _validated_capacities(
            capacities=capacities,
            currency_code=(
                normalized_currency
            ),
        )
    )

    ordered_candidates = (
        _validated_candidates(
            candidates
        )
    )

    if not ordered_candidates:
        return ()

    if not ordered_capacities:
        raise TradeReturnCapacityError(
            "return candidates exist but "
            "economic capacity is empty"
        )

    states = [
        _CapacityState(
            source=capacity
        )
        for capacity
        in ordered_capacities
    ]

    targets = []

    for candidate in ordered_candidates:
        remaining = candidate.quantity

        while remaining > ZERO:
            eligible_state = None
            future_capacity_exists = False

            for state in states:
                if (
                    state.available_quantity
                    <= ZERO
                ):
                    continue

                if (
                    state.source.event_date
                    > candidate.event_date
                ):
                    future_capacity_exists = True
                    break

                eligible_state = state
                break

            if eligible_state is None:
                if future_capacity_exists:
                    raise TradeReturnChronologyError(
                        "return date precedes the "
                        "remaining economic capacity"
                    )

                raise TradeReturnCapacityError(
                    "returned quantity exceeds "
                    "available economic capacity"
                )

            allocated_quantity = min(
                remaining,
                eligible_state
                .available_quantity,
            )

            targets.append(
                _allocate_from_capacity(
                    state=eligible_state,
                    quantity=(
                        allocated_quantity
                    ),
                    return_source_id=(
                        candidate.source_id
                    ),
                    return_date=(
                        candidate.event_date
                    ),
                    currency_code=(
                        normalized_currency
                    ),
                )
            )

            remaining -= (
                allocated_quantity
            )

    pair_keys = [
        target.pair_key
        for target in targets
    ]

    if (
        len(
            pair_keys
        )
        != len(
            set(
                pair_keys
            )
        )
    ):
        raise TradeReturnDataIntegrityError(
            "duplicate return/economic target pair"
        )

    return tuple(
        targets
    )


def _validate_commercial_state(
    *,
    gross_amount: Decimal,
    tax_amount: Decimal,
    currency_code: str,
    prefix: str,
) -> tuple[
    Decimal,
    Decimal,
]:
    gross = _money(
        gross_amount,
        currency_code=currency_code,
        field=(
            prefix
            + " gross_amount"
        ),
    )

    tax = _money(
        tax_amount,
        currency_code=currency_code,
        field=(
            prefix
            + " tax_amount"
        ),
    )

    if gross < ZERO:
        raise TradeValueCorrectionError(
            prefix
            + " gross_amount cannot be negative"
        )

    if tax < ZERO:
        raise TradeValueCorrectionError(
            prefix
            + " tax_amount cannot be negative"
        )

    if tax > gross:
        raise TradeValueCorrectionError(
            prefix
            + " tax_amount cannot exceed gross_amount"
        )

    return (
        gross,
        tax,
    )


def calculate_trade_value_correction(
    *,
    original_gross_amount: Decimal,
    original_tax_amount: Decimal,
    corrected_gross_amount: Decimal,
    corrected_tax_amount: Decimal,
    currency_code: str,
) -> TradeValueCorrectionTarget:
    """
    Calculate a signed commercial value correction.

    This function deliberately has no legal VAT side effects.
    The tax component is only preserved so the later Ukrainian
    RK lifecycle can validate and recognize the statutory state
    independently from commercial accounting.
    """

    normalized_currency = _currency(
        currency_code
    )

    (
        original_gross,
        original_tax,
    ) = _validate_commercial_state(
        gross_amount=(
            original_gross_amount
        ),
        tax_amount=(
            original_tax_amount
        ),
        currency_code=(
            normalized_currency
        ),
        prefix="original",
    )

    (
        corrected_gross,
        corrected_tax,
    ) = _validate_commercial_state(
        gross_amount=(
            corrected_gross_amount
        ),
        tax_amount=(
            corrected_tax_amount
        ),
        currency_code=(
            normalized_currency
        ),
        prefix="corrected",
    )

    gross_delta = (
        corrected_gross
        - original_gross
    )

    tax_delta = (
        corrected_tax
        - original_tax
    )

    original_base = (
        original_gross
        - original_tax
    )

    corrected_base = (
        corrected_gross
        - corrected_tax
    )

    return TradeValueCorrectionTarget(
        original_gross_amount=(
            original_gross
        ),
        original_tax_amount=(
            original_tax
        ),
        corrected_gross_amount=(
            corrected_gross
        ),
        corrected_tax_amount=(
            corrected_tax
        ),
        gross_amount_delta=(
            gross_delta
        ),
        tax_amount_delta=(
            tax_delta
        ),
        taxable_base_delta=(
            corrected_base
            - original_base
        ),
        currency_code=(
            normalized_currency
        ),
    )
