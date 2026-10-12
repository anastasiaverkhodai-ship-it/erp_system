"""Phase 13 C3-B1: deterministic deduction event ledger.

An immutable, non-posting ledger contract.

This is NOT durable PostgreSQL persistence.
This service does not authorize payroll deductions.

The eventual database adapter must provide atomic writes,
uniqueness constraints, company isolation and concurrency control.
"""

from dataclasses import dataclass
from decimal import Decimal

from app.services.payroll_deduction_remainder_service import (
    DeductionRemainder,
    DeductionRemainderError,
    apply_deduction_remainder,
    open_deduction_remainder,
)


class DeductionLedgerError(ValueError):
    """Invalid deduction ledger or replay operation."""


def _identifier(value: int, name: str) -> None:
    if type(value) is not int or value <= 0:
        raise DeductionLedgerError(
            f"{name} must be a positive integer"
        )


def _event_id(value: str) -> None:
    if (
        type(value) is not str
        or not value
        or value != value.strip()
    ):
        raise DeductionLedgerError(
            "event_id must be a nonempty trimmed string"
        )


@dataclass(frozen=True)
class DeductionLedgerEvent:
    event_id: str
    company_id: int
    instruction_id: int
    amount: Decimal

    def __post_init__(self) -> None:
        _event_id(self.event_id)

        _identifier(self.company_id, "company_id")
        _identifier(self.instruction_id, "instruction_id")

        if type(self.amount) is not Decimal:
            raise DeductionLedgerError(
                "Event amount must be Decimal"
            )

        if (
            not self.amount.is_finite()
            or self.amount <= 0
            or self.amount != self.amount.quantize(
                Decimal("0.01")
            )
        ):
            raise DeductionLedgerError(
                "Invalid event monetary amount"
            )


@dataclass(frozen=True)
class DeductionLedger:
    company_id: int
    instruction_id: int
    original_amount: Decimal
    events: tuple[DeductionLedgerEvent, ...]

    def __post_init__(self) -> None:
        _identifier(self.company_id, "company_id")
        _identifier(self.instruction_id, "instruction_id")

        if type(self.events) is not tuple:
            raise DeductionLedgerError(
                "events must be a tuple"
            )

        if type(self.original_amount) is not Decimal:
            raise DeductionLedgerError(
                "original_amount must be Decimal"
            )

        # Validate original amount and replay event chain.
        reconcile_deduction_ledger(self)


@dataclass(frozen=True)
class DeductionLedgerApplication:
    ledger: DeductionLedger
    remainder: DeductionRemainder
    applied_now: Decimal
    duplicate: bool


def reconcile_deduction_ledger(
    ledger: DeductionLedger,
) -> DeductionRemainder:
    """Rebuild the balance from the complete immutable event history."""

    if not isinstance(ledger, DeductionLedger):
        raise DeductionLedgerError(
            "A valid deduction ledger is required"
        )

    if type(ledger.events) is not tuple:
        raise DeductionLedgerError(
            "events must be a tuple"
        )

    try:
        state = open_deduction_remainder(
            company_id=ledger.company_id,
            instruction_id=ledger.instruction_id,
            original_amount=ledger.original_amount,
        )

        observed = set()

        for event in ledger.events:
            if not isinstance(event, DeductionLedgerEvent):
                raise DeductionLedgerError(
                    "Invalid ledger event"
                )

            if event.company_id != ledger.company_id:
                raise DeductionLedgerError(
                    "Cross-company ledger event"
                )

            if event.instruction_id != ledger.instruction_id:
                raise DeductionLedgerError(
                    "Instruction mismatch"
                )

            if event.event_id in observed:
                raise DeductionLedgerError(
                    "Duplicate event in ledger history"
                )

            observed.add(event.event_id)

            result = apply_deduction_remainder(
                state=state,
                company_id=ledger.company_id,
                instruction_id=ledger.instruction_id,
                event_id=event.event_id,
                allocated_amount=event.amount,
            )

            if result.duplicate:
                raise DeductionLedgerError(
                    "Historical replay is not valid"
                )

            state = result.state

        if (
            state.applied_amount + state.remaining_amount
            != state.original_amount
        ):
            raise DeductionLedgerError(
                "Ledger conservation invariant failed"
            )

        return state

    except DeductionRemainderError as exc:
        raise DeductionLedgerError(str(exc)) from exc


def open_deduction_ledger(
    *,
    company_id: int,
    instruction_id: int,
    original_amount: Decimal,
) -> DeductionLedger:
    """Create the empty ledger for one deduction instruction."""

    return DeductionLedger(
        company_id=company_id,
        instruction_id=instruction_id,
        original_amount=original_amount,
        events=(),
    )


def append_deduction_ledger_event(
    *,
    ledger: DeductionLedger,
    event: DeductionLedgerEvent,
) -> DeductionLedgerApplication:
    """Append one allocation or validate an idempotent replay.

    The same event_id with the same payload is a no-op.
    The same event_id with a different amount or scope fails.

    This method is pure and cannot protect concurrent DB writes.
    """

    if not isinstance(ledger, DeductionLedger):
        raise DeductionLedgerError(
            "A valid ledger is required"
        )

    if not isinstance(event, DeductionLedgerEvent):
        raise DeductionLedgerError(
            "A valid event is required"
        )

    state = reconcile_deduction_ledger(ledger)

    if event.company_id != ledger.company_id:
        raise DeductionLedgerError(
            "Cross-company event rejected"
        )

    if event.instruction_id != ledger.instruction_id:
        raise DeductionLedgerError(
            "Instruction mismatch"
        )

    for existing in ledger.events:
        if existing.event_id != event.event_id:
            continue

        if existing != event:
            raise DeductionLedgerError(
                "Idempotency key reused with different payload"
            )

        return DeductionLedgerApplication(
            ledger=ledger,
            remainder=state,
            applied_now=Decimal("0.00"),
            duplicate=True,
        )

    try:
        applied = apply_deduction_remainder(
            state=state,
            company_id=event.company_id,
            instruction_id=event.instruction_id,
            event_id=event.event_id,
            allocated_amount=event.amount,
        )

    except DeductionRemainderError as exc:
        raise DeductionLedgerError(str(exc)) from exc

    next_ledger = DeductionLedger(
        company_id=ledger.company_id,
        instruction_id=ledger.instruction_id,
        original_amount=ledger.original_amount,
        events=ledger.events + (event,),
    )

    rebuilt = reconcile_deduction_ledger(next_ledger)

    if rebuilt != applied.state:
        raise DeductionLedgerError(
            "Replay result does not match application"
        )

    return DeductionLedgerApplication(
        ledger=next_ledger,
        remainder=rebuilt,
        applied_now=event.amount,
        duplicate=False,
    )
