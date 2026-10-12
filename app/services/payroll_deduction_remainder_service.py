"""Phase 13 C3-A: pure, non-posting deduction remainder controls.

This service tracks a single instruction's technical outstanding amount
across allocation events. It does not execute payroll, establish legal
entitlement, authorize a deduction, or persist balances.

Persisted replay protection and authoritative legal source binding
must be implemented separately before production activation.
"""

from dataclasses import dataclass
from decimal import Decimal


CENT = Decimal("0.01")


class DeductionRemainderError(ValueError):
    """Invalid deduction balance or allocation transition."""


def _positive_id(value: int, field: str) -> None:
    if type(value) is not int or value <= 0:
        raise DeductionRemainderError(
            f"{field} must be a positive integer"
        )


def _amount(
    value: Decimal,
    field: str,
    *,
    allow_zero: bool = True,
) -> Decimal:
    if type(value) is not Decimal:
        raise DeductionRemainderError(
            f"{field} must be Decimal"
        )

    if not value.is_finite():
        raise DeductionRemainderError(
            f"{field} must be finite"
        )

    if value < 0 or (not allow_zero and value == 0):
        raise DeductionRemainderError(
            f"{field} has an invalid amount"
        )

    if value != value.quantize(CENT):
        raise DeductionRemainderError(
            f"{field} must use monetary cent precision"
        )

    return value


@dataclass(frozen=True)
class DeductionRemainder:
    company_id: int
    instruction_id: int
    original_amount: Decimal
    applied_amount: Decimal
    remaining_amount: Decimal
    applied_event_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        _positive_id(self.company_id, "company_id")
        _positive_id(self.instruction_id, "instruction_id")

        original = _amount(
            self.original_amount,
            "original_amount",
        )
        applied = _amount(
            self.applied_amount,
            "applied_amount",
        )
        remaining = _amount(
            self.remaining_amount,
            "remaining_amount",
        )

        if applied + remaining != original:
            raise DeductionRemainderError(
                "Remainder conservation invariant failed"
            )

        if type(self.applied_event_ids) is not tuple:
            raise DeductionRemainderError(
                "applied_event_ids must be a tuple"
            )

        for event_id in self.applied_event_ids:
            if (
                type(event_id) is not str
                or not event_id.strip()
                or event_id != event_id.strip()
            ):
                raise DeductionRemainderError(
                    "Invalid applied event identifier"
                )

        if len(set(self.applied_event_ids)) != len(
            self.applied_event_ids
        ):
            raise DeductionRemainderError(
                "Duplicate applied event identifiers"
            )


@dataclass(frozen=True)
class RemainderApplication:
    state: DeductionRemainder
    event_id: str
    applied_now: Decimal
    duplicate: bool


def open_deduction_remainder(
    *,
    company_id: int,
    instruction_id: int,
    original_amount: Decimal,
) -> DeductionRemainder:
    """Create an immutable, technical opening remainder."""

    _positive_id(company_id, "company_id")
    _positive_id(instruction_id, "instruction_id")

    original = _amount(
        original_amount,
        "original_amount",
    )

    return DeductionRemainder(
        company_id=company_id,
        instruction_id=instruction_id,
        original_amount=original,
        applied_amount=Decimal("0.00"),
        remaining_amount=original,
        applied_event_ids=(),
    )


def apply_deduction_remainder(
    *,
    state: DeductionRemainder,
    company_id: int,
    instruction_id: int,
    event_id: str,
    allocated_amount: Decimal,
) -> RemainderApplication:
    """Apply one non-posting allocation to an immutable remainder.

    Duplicate event identifiers are accepted only for a strict,
    idempotent no-op. The caller must not interpret a duplicate as
    authorization to create a new payroll deduction.

    This is in-memory replay prevention, not durable idempotency.
    """

    if not isinstance(state, DeductionRemainder):
        raise DeductionRemainderError(
            "A valid deduction remainder is required"
        )

    _positive_id(company_id, "company_id")
    _positive_id(instruction_id, "instruction_id")

    if company_id != state.company_id:
        raise DeductionRemainderError(
            "Deduction belongs to another company"
        )

    if instruction_id != state.instruction_id:
        raise DeductionRemainderError(
            "Deduction instruction mismatch"
        )

    if (
        type(event_id) is not str
        or not event_id.strip()
        or event_id != event_id.strip()
    ):
        raise DeductionRemainderError(
            "A nonempty event_id is required"
        )

    allocated = _amount(
        allocated_amount,
        "allocated_amount",
        allow_zero=False,
    )

    if event_id in state.applied_event_ids:
        return RemainderApplication(
            state=state,
            event_id=event_id,
            applied_now=Decimal("0.00"),
            duplicate=True,
        )

    if allocated > state.remaining_amount:
        raise DeductionRemainderError(
            "Allocation exceeds outstanding remainder"
        )

    next_state = DeductionRemainder(
        company_id=state.company_id,
        instruction_id=state.instruction_id,
        original_amount=state.original_amount,
        applied_amount=(
            state.applied_amount + allocated
        ),
        remaining_amount=(
            state.remaining_amount - allocated
        ),
        applied_event_ids=(
            state.applied_event_ids + (event_id,)
        ),
    )

    return RemainderApplication(
        state=next_state,
        event_id=event_id,
        applied_now=allocated,
        duplicate=False,
    )
