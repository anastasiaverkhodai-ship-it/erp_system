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
from app.services.trade_return_calculation_service import (
    TradeReturnCandidate,
    TradeReturnEconomicCapacity,
    build_trade_return_targets,
)


ZERO = Decimal("0")


class PurchaseReturnRecognitionCalculationError(
    Exception
):
    """Base Purchase Return recognition calculation error."""


class PurchaseReturnRecognitionDataIntegrityError(
    PurchaseReturnRecognitionCalculationError
):
    """Purchase Return economic source data is inconsistent."""


@dataclass(
    frozen=True,
    slots=True,
)
class PurchaseReturnEconomicCapacity:
    """
    One immutable PURCHASE economic capacity.

    source_id
        InvoiceFulfillmentAllocation.id.

    base_amount
        Authoritative VAT-exclusive historical receipt-accounting
        base assigned to this allocation.

    gross_amount / tax_amount
        Commercial/tax snapshots used only for Return allocation.
        They do not authorize or post VAT correction.

    base_amount is deliberately independent from gross_amount and
    tax_amount. It must never be reconstructed as gross minus tax.
    """

    source_id: int
    event_date: date
    quantity: Decimal
    base_amount: Decimal
    gross_amount: Decimal
    tax_amount: Decimal
    currency_code: str


@dataclass(
    frozen=True,
    slots=True,
)
class PurchaseReturnRecognitionTarget:
    """
    Desired immutable economic Purchase Return state for one pair:

        TradeReturnEvent
        +
        InvoiceFulfillmentAllocation
    """

    return_source_id: int
    economic_source_id: int
    event_date: date
    quantity: Decimal
    base_amount: Decimal
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


@dataclass(
    slots=True,
)
class _BaseState:
    source: PurchaseReturnEconomicCapacity
    returned_quantity: Decimal = ZERO
    returned_base_amount: Decimal = ZERO


def _decimal(
    value,
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
        raise (
            PurchaseReturnRecognitionDataIntegrityError(
                f"{field} must be a finite Decimal"
            )
        ) from exc

    if not result.is_finite():
        raise (
            PurchaseReturnRecognitionDataIntegrityError(
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
            PurchaseReturnRecognitionDataIntegrityError(
                f"{field} must be greater than zero"
            )
        )

    return value


def _business_date(
    value: date,
    *,
    field: str,
) -> date:
    if not isinstance(
        value,
        date,
    ):
        raise (
            PurchaseReturnRecognitionDataIntegrityError(
                f"{field} must be a date"
            )
        )

    return value


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
        raise (
            PurchaseReturnRecognitionDataIntegrityError(
                "currency_code must contain exactly "
                "three alphabetic characters"
            )
        )

    return normalized


def _money(
    value,
    *,
    currency_code: str,
    field: str,
) -> Decimal:
    amount = _decimal(
        value,
        field=field,
    )

    try:
        return round_currency_amount(
            amount=amount,
            currency_code=currency_code,
        )
    except Exception as exc:
        raise (
            PurchaseReturnRecognitionDataIntegrityError(
                f"{field} cannot be rounded"
            )
        ) from exc


def _normalize_capacity(
    value: PurchaseReturnEconomicCapacity,
    *,
    expected_currency: str,
) -> PurchaseReturnEconomicCapacity:
    if not isinstance(
        value,
        PurchaseReturnEconomicCapacity,
    ):
        raise (
            PurchaseReturnRecognitionDataIntegrityError(
                "capacities must contain "
                "PurchaseReturnEconomicCapacity"
            )
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
        raise (
            PurchaseReturnRecognitionDataIntegrityError(
                "economic quantity must be greater than zero"
            )
        )

    currency_code = _currency(
        value.currency_code
    )

    if currency_code != expected_currency:
        raise (
            PurchaseReturnRecognitionDataIntegrityError(
                "economic source currency mismatch"
            )
        )

    base_amount = _money(
        value.base_amount,
        currency_code=currency_code,
        field="economic base_amount",
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

    if base_amount < ZERO:
        raise (
            PurchaseReturnRecognitionDataIntegrityError(
                "economic base_amount cannot be negative"
            )
        )

    if gross_amount <= ZERO:
        raise (
            PurchaseReturnRecognitionDataIntegrityError(
                "economic gross_amount must be greater than zero"
            )
        )

    if tax_amount < ZERO:
        raise (
            PurchaseReturnRecognitionDataIntegrityError(
                "economic tax_amount cannot be negative"
            )
        )

    if tax_amount > gross_amount:
        raise (
            PurchaseReturnRecognitionDataIntegrityError(
                "economic tax_amount cannot exceed gross_amount"
            )
        )

    return PurchaseReturnEconomicCapacity(
        source_id=source_id,
        event_date=event_date,
        quantity=quantity,
        base_amount=base_amount,
        gross_amount=gross_amount,
        tax_amount=tax_amount,
        currency_code=currency_code,
    )


def _cumulative_base(
    *,
    total_base: Decimal,
    total_quantity: Decimal,
    cumulative_quantity: Decimal,
    currency_code: str,
) -> Decimal:
    if cumulative_quantity < ZERO:
        raise (
            PurchaseReturnRecognitionDataIntegrityError(
                "cumulative returned quantity cannot be negative"
            )
        )

    if cumulative_quantity > total_quantity:
        raise (
            PurchaseReturnRecognitionDataIntegrityError(
                "cumulative returned quantity exceeds "
                "purchase economic capacity"
            )
        )

    if cumulative_quantity == ZERO:
        return round_currency_amount(
            amount=ZERO,
            currency_code=currency_code,
        )

    if cumulative_quantity == total_quantity:
        return total_base

    return round_currency_amount(
        amount=(
            total_base
            * cumulative_quantity
            / total_quantity
        ),
        currency_code=currency_code,
    )


def build_purchase_return_recognition_targets(
    *,
    capacities: Iterable[
        PurchaseReturnEconomicCapacity
    ],
    candidates: Iterable[
        TradeReturnCandidate
    ],
    currency_code: str,
) -> tuple[
    PurchaseReturnRecognitionTarget,
    ...,
]:
    """
    Allocate PURCHASE Return facts against immutable purchase
    economic capacities.

    FIFO quantity allocation plus gross/tax slicing is delegated to
    the existing direction-agnostic Trade Return calculator.

    Historical purchase receipt base is allocated independently with
    cumulative-delta currency rounding from capacity.base_amount.

    This separation is mandatory:
        authoritative base != derived gross-minus-tax amount.

    This function performs pure calculation only. It does not:
    - persist events;
    - create JournalEntry;
    - modify warehouse quantity;
    - mutate supplier advances;
    - recognize/reverse VAT;
    - create an RK.
    """

    normalized_currency = _currency(
        currency_code
    )

    normalized_capacities = []
    seen_source_ids = set()

    for raw in capacities:
        capacity = _normalize_capacity(
            raw,
            expected_currency=normalized_currency,
        )

        if capacity.source_id in seen_source_ids:
            raise (
                PurchaseReturnRecognitionDataIntegrityError(
                    "duplicate purchase economic source_id"
                )
            )

        seen_source_ids.add(
            capacity.source_id
        )

        normalized_capacities.append(
            capacity
        )

    normalized_capacities = tuple(
        normalized_capacities
    )

    generic_capacities = tuple(
        TradeReturnEconomicCapacity(
            source_id=capacity.source_id,
            event_date=capacity.event_date,
            quantity=capacity.quantity,
            gross_amount=capacity.gross_amount,
            tax_amount=capacity.tax_amount,
            currency_code=capacity.currency_code,
        )
        for capacity
        in normalized_capacities
    )

    generic_targets = build_trade_return_targets(
        capacities=generic_capacities,
        candidates=candidates,
        currency_code=normalized_currency,
    )

    states = {
        capacity.source_id: _BaseState(
            source=capacity
        )
        for capacity
        in normalized_capacities
    }

    targets = []

    for generic_target in generic_targets:
        state = states.get(
            generic_target.economic_source_id
        )

        if state is None:
            raise (
                PurchaseReturnRecognitionDataIntegrityError(
                    "calculated target references unknown "
                    "purchase economic source"
                )
            )

        cumulative_quantity = (
            state.returned_quantity
            + generic_target.quantity
        )

        cumulative_base = _cumulative_base(
            total_base=state.source.base_amount,
            total_quantity=state.source.quantity,
            cumulative_quantity=cumulative_quantity,
            currency_code=normalized_currency,
        )

        base_slice = (
            cumulative_base
            - state.returned_base_amount
        )

        if base_slice < ZERO:
            raise (
                PurchaseReturnRecognitionDataIntegrityError(
                    "allocated historical base cannot be negative"
                )
            )

        state.returned_quantity = (
            cumulative_quantity
        )

        state.returned_base_amount = (
            cumulative_base
        )

        targets.append(
            PurchaseReturnRecognitionTarget(
                return_source_id=(
                    generic_target.return_source_id
                ),
                economic_source_id=(
                    generic_target.economic_source_id
                ),
                event_date=(
                    generic_target.event_date
                ),
                quantity=(
                    generic_target.quantity
                ),
                base_amount=base_slice,
                gross_amount=(
                    generic_target.gross_amount
                ),
                tax_amount=(
                    generic_target.tax_amount
                ),
                currency_code=(
                    generic_target.currency_code
                ),
            )
        )

    return tuple(
        targets
    )
