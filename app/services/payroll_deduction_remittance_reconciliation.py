"""Phase 13 C4-D2: independent technical reconciliation.

Three distinct layers:

1. Original deduction instruction.
2. Technical deduction remainder ledger.
3. Technical remittance allocation plan.

IMPORTANT:

An allocation event does not prove that money was withheld.
A remittance event does not prove that a payment occurred.

This module cannot authorize payroll, deductions, accounting
postings, remittances or bank transfers.

It does not access or modify the database.
"""

from dataclasses import dataclass
from decimal import Decimal


CENT = Decimal("0.01")


class RemittanceReconciliationError(ValueError):
    """Inconsistent technical reconciliation evidence."""


def _identity(value, name):
    if type(value) is not int or value <= 0:
        raise RemittanceReconciliationError(
            f"{name} must be a positive integer"
        )


def _currency(value):
    if (
        type(value) is not str
        or len(value) != 3
        or not value.isascii()
        or not value.isalpha()
        or value != value.upper()
    ):
        raise RemittanceReconciliationError(
            "Invalid currency_code"
        )


def _amount(value, name):
    if not isinstance(value, Decimal):
        raise RemittanceReconciliationError(
            f"{name} must be Decimal"
        )

    if not value.is_finite():
        raise RemittanceReconciliationError(
            f"{name} must be finite"
        )

    if value < 0:
        raise RemittanceReconciliationError(
            f"{name} cannot be negative"
        )

    if value != value.quantize(CENT):
        raise RemittanceReconciliationError(
            f"{name} exceeds two decimal places"
        )


def _count(value, name):
    if type(value) is not int or value < 0:
        raise RemittanceReconciliationError(
            f"{name} must be a nonnegative integer"
        )


@dataclass(frozen=True)
class InstructionEvidence:
    company_id: int
    instruction_id: int
    original_amount: Decimal
    currency_code: str


@dataclass(frozen=True)
class RemainderEvidence:
    company_id: int
    instruction_id: int
    original_amount: Decimal
    currency_code: str
    applied_amount: Decimal
    remaining_amount: Decimal
    event_count: int


@dataclass(frozen=True)
class RemittanceEvidence:
    company_id: int
    instruction_id: int
    beneficiary_reference: str
    currency_code: str
    available_amount: Decimal
    allocated_amount: Decimal
    remaining_amount: Decimal
    event_count: int


@dataclass(frozen=True)
class ReconciliationResult:
    company_id: int
    instruction_id: int
    currency_code: str
    beneficiary_reference: str

    instruction_amount: Decimal

    remainder_original_amount: Decimal
    remainder_applied_amount: Decimal
    remainder_remaining_amount: Decimal
    remainder_event_count: int

    remittance_ceiling: Decimal
    remittance_allocated_amount: Decimal
    remittance_remaining_amount: Decimal
    remittance_event_count: int

    technical_reconciled: bool = True

    # No source currently proves real withheld funds or payments.
    withheld_funds_verified: bool = False
    bank_payment_verified: bool = False

    payment_execution_allowed: bool = False
    payroll_posting_allowed: bool = False
    legal_authorization: str = "NOT_AUTHORIZED"


def reconcile_remittance_sources(
    *,
    instruction: InstructionEvidence,
    remainder: RemainderEvidence,
    remittance: RemittanceEvidence,
) -> ReconciliationResult:
    """Compare three independently obtained technical snapshots.

    The caller must obtain authoritative snapshots and independently
    aggregate event rows. This pure function rejects contradictory
    identities, amounts, currencies and arithmetic.

    Successful reconciliation means TECHNICAL consistency only.
    """

    if not isinstance(instruction, InstructionEvidence):
        raise RemittanceReconciliationError(
            "Invalid instruction evidence"
        )

    if not isinstance(remainder, RemainderEvidence):
        raise RemittanceReconciliationError(
            "Invalid remainder evidence"
        )

    if not isinstance(remittance, RemittanceEvidence):
        raise RemittanceReconciliationError(
            "Invalid remittance evidence"
        )

    for item in (instruction, remainder, remittance):
        _identity(item.company_id, "company_id")
        _identity(item.instruction_id, "instruction_id")
        _currency(item.currency_code)

    if not (
        instruction.company_id
        == remainder.company_id
        == remittance.company_id
    ):
        raise RemittanceReconciliationError(
            "Company isolation mismatch"
        )

    if not (
        instruction.instruction_id
        == remainder.instruction_id
        == remittance.instruction_id
    ):
        raise RemittanceReconciliationError(
            "Instruction identity mismatch"
        )

    if not (
        instruction.currency_code
        == remainder.currency_code
        == remittance.currency_code
    ):
        raise RemittanceReconciliationError(
            "Currency mismatch"
        )

    beneficiary = remittance.beneficiary_reference

    if (
        type(beneficiary) is not str
        or not beneficiary
        or beneficiary != beneficiary.strip()
        or len(beneficiary) > 120
        or any(ord(c) < 32 for c in beneficiary)
    ):
        raise RemittanceReconciliationError(
            "Invalid beneficiary reference"
        )

    amounts = (
        (instruction.original_amount, "instruction amount"),
        (remainder.original_amount, "remainder opening"),
        (remainder.applied_amount, "remainder applied"),
        (remainder.remaining_amount, "remainder remaining"),
        (remittance.available_amount, "remittance ceiling"),
        (remittance.allocated_amount, "remittance allocated"),
        (remittance.remaining_amount, "remittance remaining"),
    )

    for value, name in amounts:
        _amount(value, name)

    _count(remainder.event_count, "remainder event_count")
    _count(remittance.event_count, "remittance event_count")

    if remainder.original_amount != instruction.original_amount:
        raise RemittanceReconciliationError(
            "Remainder opening differs from instruction"
        )

    if (
        remainder.applied_amount
        + remainder.remaining_amount
        != remainder.original_amount
    ):
        raise RemittanceReconciliationError(
            "Remainder conservation mismatch"
        )

    if remainder.applied_amount > remainder.original_amount:
        raise RemittanceReconciliationError(
            "Remainder allocation exceeds original amount"
        )

    if (
        remittance.allocated_amount
        + remittance.remaining_amount
        != remittance.available_amount
    ):
        raise RemittanceReconciliationError(
            "Remittance conservation mismatch"
        )

    if remittance.allocated_amount > remittance.available_amount:
        raise RemittanceReconciliationError(
            "Remittance allocation exceeds plan ceiling"
        )

    if remittance.available_amount > instruction.original_amount:
        raise RemittanceReconciliationError(
            "Remittance ceiling exceeds instruction amount"
        )

    # These event-count checks are sanity guards, not substitutes
    # for independently verifying PostgreSQL event uniqueness.
    if remainder.event_count == 0 and remainder.applied_amount != 0:
        raise RemittanceReconciliationError(
            "Remainder applied without events"
        )

    if remittance.event_count == 0 and remittance.allocated_amount != 0:
        raise RemittanceReconciliationError(
            "Remittance allocated without events"
        )

    if remainder.event_count > 0 and remainder.applied_amount == 0:
        raise RemittanceReconciliationError(
            "Remainder events have zero aggregate"
        )

    if remittance.event_count > 0 and remittance.allocated_amount == 0:
        raise RemittanceReconciliationError(
            "Remittance events have zero aggregate"
        )

    return ReconciliationResult(
        company_id=instruction.company_id,
        instruction_id=instruction.instruction_id,
        currency_code=instruction.currency_code,
        beneficiary_reference=beneficiary,

        instruction_amount=instruction.original_amount,

        remainder_original_amount=remainder.original_amount,
        remainder_applied_amount=remainder.applied_amount,
        remainder_remaining_amount=remainder.remaining_amount,
        remainder_event_count=remainder.event_count,

        remittance_ceiling=remittance.available_amount,
        remittance_allocated_amount=remittance.allocated_amount,
        remittance_remaining_amount=remittance.remaining_amount,
        remittance_event_count=remittance.event_count,
    )
