"""Pure physical cash chronology calculations.

This module does not read PostgreSQL, approve an opening,
or post financial movements. The caller must provide movements
from authoritative, transaction-locked sources.

Never count a cash document as an additional money movement.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal


CENT = Decimal("0.01")

SOURCE_KINDS = frozenset({
    "payment_issue",
    "payment_reversal",
    "payroll_issue",
    "payroll_reversal",
})


class CashChronologyError(ValueError):
    pass


class CashInsufficientFundsError(CashChronologyError):
    pass


def _money(value: Decimal, *, allow_zero: bool) -> Decimal:
    if not isinstance(value, Decimal):
        raise CashChronologyError(
            "Money must be supplied as Decimal"
        )
    if not value.is_finite():
        raise CashChronologyError(
            "Money must be finite"
        )
    if value.as_tuple().exponent < -2:
        raise CashChronologyError(
            "Money must have at most two decimal places"
        )
    if value < 0 or (not allow_zero and value == 0):
        raise CashChronologyError(
            "Invalid physical cash amount"
        )
    return value.quantize(CENT)


@dataclass(frozen=True)
class CashOpening:
    company_id: int
    cash_desk_id: int
    currency_code: str
    effective_date: date
    amount: Decimal
    evidence_id: int


@dataclass(frozen=True)
class CashMovement:
    company_id: int
    cash_desk_id: int
    currency_code: str
    event_date: date
    sequence: int
    source_kind: str
    source_id: int
    direction: str
    amount: Decimal


@dataclass(frozen=True)
class CashBalancePoint:
    event_date: date
    source_kind: str
    source_id: int
    balance: Decimal


@dataclass(frozen=True)
class CashChronologyResult:
    opening_balance: Decimal
    closing_balance: Decimal
    points: tuple[CashBalancePoint, ...]


def calculate_cash_chronology(
    *,
    opening: CashOpening,
    movements: tuple[CashMovement, ...],
) -> CashChronologyResult:
    """Reject any movement that makes physical cash negative.

    The sequence is an explicit deterministic ordering within each
    day. A future database adapter must derive this from the
    authoritative posting order and serialize all same-desk writers.

    A CashOpening is ONLY a calculation input. This function does
    NOT verify or authorize its provenance.
    """

    if (
        opening.company_id <= 0
        or opening.cash_desk_id <= 0
        or opening.evidence_id <= 0
        or not isinstance(opening.effective_date, date)
        or len(opening.currency_code) != 3
        or not opening.currency_code.isalpha()
        or opening.currency_code != opening.currency_code.upper()
    ):
        raise CashChronologyError(
            "Invalid physical cash opening identity"
        )

    balance = _money(opening.amount, allow_zero=True)

    identities = set()
    ordering = set()

    for event in movements:
        if (
            event.company_id != opening.company_id
            or event.cash_desk_id != opening.cash_desk_id
            or event.currency_code != opening.currency_code
        ):
            raise CashChronologyError(
                "Cross-company, desk or currency cash movement"
            )

        if (
            not isinstance(event.event_date, date)
            or event.event_date < opening.effective_date
        ):
            raise CashChronologyError(
                "Movement precedes verified cash opening cutover"
            )

        if (
            type(event.sequence) is not int
            or event.sequence <= 0
            or type(event.source_id) is not int
            or event.source_id <= 0
            or event.source_kind not in SOURCE_KINDS
            or event.direction not in ("incoming", "outgoing")
        ):
            raise CashChronologyError(
                "Invalid cash movement identity or direction"
            )

        identity = (event.source_kind, event.source_id)
        position = (event.event_date, event.sequence)

        if identity in identities:
            raise CashChronologyError(
                "Duplicate physical cash source event"
            )

        if position in ordering:
            raise CashChronologyError(
                "Ambiguous physical cash event ordering"
            )

        identities.add(identity)
        ordering.add(position)

        _money(event.amount, allow_zero=False)

    points = []

    for event in sorted(
        movements,
        key=lambda item: (item.event_date, item.sequence),
    ):
        amount = _money(event.amount, allow_zero=False)

        if event.direction == "incoming":
            balance += amount
        else:
            balance -= amount

        if balance < 0:
            raise CashInsufficientFundsError(
                "Physical cash would become negative: "
                f"{event.event_date.isoformat()} "
                f"{event.source_kind} #{event.source_id}"
            )

        points.append(
            CashBalancePoint(
                event_date=event.event_date,
                source_kind=event.source_kind,
                source_id=event.source_id,
                balance=balance,
            )
        )

    return CashChronologyResult(
        opening_balance=_money(opening.amount, allow_zero=True),
        closing_balance=balance,
        points=tuple(points),
    )
