"""Phase 13 C3-C1: payroll deduction source reconciliation.

Pure technical reconciliation of an immutable source snapshot
against a deduction remainder event ledger.

This is NOT legal authorization to execute deductions.

No database writes.
No payroll postings.
No remittances.
No transaction commit or rollback.
"""

from dataclasses import dataclass
from decimal import Decimal

from app.services.payroll_deduction_remainder_ledger import (
    DeductionLedger,
    DeductionLedgerError,
    reconcile_deduction_ledger,
)


CENT = Decimal("0.01")


class DeductionSourceReconciliationError(ValueError):
    """Source and technical remainder history do not reconcile."""


def _positive_id(value, field):
    if type(value) is not int or value <= 0:
        raise DeductionSourceReconciliationError(
            f"{field} must be a positive integer"
        )


def _money(value, field):
    if type(value) is not Decimal:
        raise DeductionSourceReconciliationError(
            f"{field} must be Decimal"
        )

    if not value.is_finite():
        raise DeductionSourceReconciliationError(
            f"{field} must be finite"
        )

    if value < 0:
        raise DeductionSourceReconciliationError(
            f"{field} cannot be negative"
        )

    if value != value.quantize(CENT):
        raise DeductionSourceReconciliationError(
            f"{field} must have at most two decimal places"
        )

    return value


def _currency(value, field):
    if (
        type(value) is not str
        or len(value) != 3
        or not value.isascii()
        or not value.isalpha()
        or value != value.upper()
    ):
        raise DeductionSourceReconciliationError(
            f"{field} must be an uppercase ISO-like "
            "three-letter currency code"
        )


@dataclass(frozen=True)
class DeductionSourceSnapshot:
    company_id: int
    instruction_id: int
    original_amount: Decimal
    currency_code: str

    def __post_init__(self):
        _positive_id(self.company_id, "company_id")
        _positive_id(self.instruction_id, "instruction_id")
        _money(self.original_amount, "original_amount")
        _currency(self.currency_code, "currency_code")


@dataclass(frozen=True)
class DeductionReconciliationResult:
    company_id: int
    instruction_id: int
    currency_code: str
    original_amount: Decimal
    applied_amount: Decimal
    remaining_amount: Decimal
    event_count: int
    reconciled: bool = True
    payroll_posting_allowed: bool = False
    legal_authorization: str = "NOT_AUTHORIZED"


def reconcile_deduction_source(
    *,
    source: DeductionSourceSnapshot,
    ledger: DeductionLedger,
    stored_currency_code: str,
) -> DeductionReconciliationResult:
    """Validate identity, currency, history and money conservation.

    This operation does not authorize or carry out payroll deductions.

    The original source snapshot is treated as authoritative.
    The ledger is independently reconciled from its complete events.
    """

    if not isinstance(source, DeductionSourceSnapshot):
        raise DeductionSourceReconciliationError(
            "Invalid source snapshot"
        )

    if not isinstance(ledger, DeductionLedger):
        raise DeductionSourceReconciliationError(
            "Invalid ledger"
        )

    _currency(stored_currency_code, "stored_currency_code")

    if ledger.company_id != source.company_id:
        raise DeductionSourceReconciliationError(
            "Company isolation mismatch"
        )

    if ledger.instruction_id != source.instruction_id:
        raise DeductionSourceReconciliationError(
            "Instruction identity mismatch"
        )

    if ledger.original_amount != source.original_amount:
        raise DeductionSourceReconciliationError(
            "Original amount mismatch"
        )

    if stored_currency_code != source.currency_code:
        raise DeductionSourceReconciliationError(
            "Currency mismatch"
        )

    try:
        state = reconcile_deduction_ledger(ledger)

    except DeductionLedgerError as exc:
        raise DeductionSourceReconciliationError(
            "Ledger integrity mismatch: " + str(exc)
        ) from exc

    original = _money(
        state.original_amount,
        "ledger original_amount",
    )

    applied = _money(
        state.applied_amount,
        "ledger applied_amount",
    )

    remaining = _money(
        state.remaining_amount,
        "ledger remaining_amount",
    )

    if original != source.original_amount:
        raise DeductionSourceReconciliationError(
            "Reconstructed original amount mismatch"
        )

    if applied + remaining != original:
        raise DeductionSourceReconciliationError(
            "Balance conservation mismatch"
        )

    events = tuple(ledger.events)

    if len(events) != len({
        event.event_id for event in events
    }):
        raise DeductionSourceReconciliationError(
            "Duplicate ledger event identity"
        )

    return DeductionReconciliationResult(
        company_id=source.company_id,
        instruction_id=source.instruction_id,
        currency_code=source.currency_code,
        original_amount=original,
        applied_amount=applied,
        remaining_amount=remaining,
        event_count=len(events),
    )
