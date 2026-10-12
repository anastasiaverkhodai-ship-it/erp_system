"""Phase 13 C3-B2-C2: technical deduction remainder persistence.

This service provides transaction-scoped storage only.

Legal authorization: NOT_AUTHORIZED.
Payroll posting: disabled.

The caller owns the transaction, commit and rollback.
Do not invoke this service from production payroll posting flows.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.payroll_deduction import PayrollDeductionInstruction
from app.models.payroll_deduction_remainder import (
    PayrollDeductionRemainder,
    PayrollDeductionRemainderEvent,
)
from app.services.payroll_deduction_remainder_ledger import (
    DeductionLedger,
    DeductionLedgerEvent,
    DeductionLedgerError,
    append_deduction_ledger_event,
    reconcile_deduction_ledger,
)


CENT = Decimal("0.01")


class DeductionPersistenceError(ValueError):
    """A technical ledger operation failed validation."""


@dataclass(frozen=True)
class PersistedRemainderPreview:
    remainder_id: int
    company_id: int
    instruction_id: int
    currency_code: str
    original_amount: Decimal
    applied_amount: Decimal
    remaining_amount: Decimal
    event_count: int
    ledger: DeductionLedger
    payroll_posting_allowed: bool = False
    legal_authorization: str = "NOT_AUTHORIZED"


@dataclass(frozen=True)
class PersistedLedgerApplication:
    preview: PersistedRemainderPreview
    applied_now: Decimal
    duplicate: bool


def _id(value, field):
    if type(value) is not int or value <= 0:
        raise DeductionPersistenceError(
            f"{field} must be a positive integer"
        )


def _money(value, field, *, allow_zero=True):
    if type(value) is not Decimal:
        raise DeductionPersistenceError(
            f"{field} must be Decimal"
        )

    if (
        not value.is_finite()
        or value < 0
        or (not allow_zero and value == 0)
        or value != value.quantize(CENT)
    ):
        raise DeductionPersistenceError(
            f"Invalid {field}"
        )

    return value


def _currency(value):
    if (
        type(value) is not str
        or len(value) != 3
        or not value.isascii()
        or not value.isalpha()
        or value != value.upper()
    ):
        raise DeductionPersistenceError(
            "Invalid currency_code"
        )


def _event_id(value):
    if (
        type(value) is not str
        or not value
        or value != value.strip()
        or len(value) > 100
    ):
        raise DeductionPersistenceError(
            "Invalid event_id"
        )


async def _lock_instruction(
    db: AsyncSession,
    company_id: int,
    instruction_id: int,
):
    """Serialize writers for the same company/instruction.

    The advisory transaction lock serializes operations
    even when a newly inserted instruction is not yet
    visible to another PostgreSQL transaction.

    SELECT FOR UPDATE additionally protects the committed
    parent row from concurrent changes.

    PostgreSQL releases the advisory lock automatically
    when the caller-owned transaction ends.
    """

    _id(company_id, "company_id")
    _id(instruction_id, "instruction_id")

    # Transaction-level lock. No manual unlock is needed.
    #
    # Use TWO int4 identifiers to prevent collisions caused
    # by combining company/instruction IDs into one integer.
    #
    # This operation must use the SAME AsyncSession and
    # transaction as the subsequent SELECT FOR UPDATE.

    await db.execute(
        text(
            "SELECT pg_advisory_xact_lock("
            ":company_id, :instruction_id)"
        ),
        {
            "company_id": company_id,
            "instruction_id": instruction_id,
        },
    )

    result = await db.execute(
        select(PayrollDeductionInstruction)
        .where(
            PayrollDeductionInstruction.company_id
            == company_id,
            PayrollDeductionInstruction.id
            == instruction_id,
        )
        .with_for_update()
    )

    row = result.scalar_one_or_none()

    if row is None:
        raise DeductionPersistenceError(
            "Instruction not found in company"
        )

    return row


async def _lock_opening(
    db: AsyncSession,
    company_id: int,
    instruction_id: int,
):
    result = await db.execute(
        select(PayrollDeductionRemainder)
        .where(
            PayrollDeductionRemainder.company_id
            == company_id,
            PayrollDeductionRemainder.instruction_id
            == instruction_id,
        )
        .with_for_update()
    )

    return result.scalar_one_or_none()


async def _preview_from_opening(
    db: AsyncSession,
    opening: PayrollDeductionRemainder,
):
    result = await db.execute(
        select(PayrollDeductionRemainderEvent)
        .where(
            PayrollDeductionRemainderEvent.company_id
            == opening.company_id,
            PayrollDeductionRemainderEvent.remainder_id
            == opening.id,
        )
        .order_by(PayrollDeductionRemainderEvent.id)
    )

    rows = result.scalars().all()

    try:
        ledger = DeductionLedger(
            company_id=opening.company_id,
            instruction_id=opening.instruction_id,
            original_amount=opening.original_amount,
            events=tuple(
                DeductionLedgerEvent(
                    event_id=row.event_id,
                    company_id=row.company_id,
                    instruction_id=opening.instruction_id,
                    amount=row.amount,
                )
                for row in rows
            ),
        )

        state = reconcile_deduction_ledger(ledger)

    except DeductionLedgerError as exc:
        raise DeductionPersistenceError(
            "Persisted ledger reconciliation failed: "
            + str(exc)
        ) from exc

    return PersistedRemainderPreview(
        remainder_id=opening.id,
        company_id=opening.company_id,
        instruction_id=opening.instruction_id,
        currency_code=opening.currency_code,
        original_amount=state.original_amount,
        applied_amount=state.applied_amount,
        remaining_amount=state.remaining_amount,
        event_count=len(rows),
        ledger=ledger,
    )


async def open_persisted_remainder(
    *,
    db: AsyncSession,
    company_id: int,
    instruction_id: int,
    original_amount: Decimal,
    currency_code: str,
    actor_id: int,
) -> PersistedRemainderPreview:
    """Create or strictly replay an immutable opening."""

    _id(company_id, "company_id")
    _id(instruction_id, "instruction_id")
    _id(actor_id, "actor_id")

    amount = _money(original_amount, "original_amount")
    _currency(currency_code)

    instruction = await _lock_instruction(
        db, company_id, instruction_id
    )

    if instruction.currency_code != currency_code:
        raise DeductionPersistenceError(
            "Instruction currency mismatch"
        )

    opening = await _lock_opening(
        db, company_id, instruction_id
    )

    if opening is None:
        opening = PayrollDeductionRemainder(
            company_id=company_id,
            instruction_id=instruction_id,
            original_amount=amount,
            currency_code=currency_code,
            created_by=actor_id,
            created_at=datetime.now(timezone.utc),
        )

        db.add(opening)
        await db.flush()

    elif (
        opening.original_amount != amount
        or opening.currency_code != currency_code
    ):
        raise DeductionPersistenceError(
            "Existing opening payload mismatch"
        )

    return await _preview_from_opening(db, opening)


async def preview_persisted_remainder(
    *,
    db: AsyncSession,
    company_id: int,
    instruction_id: int,
) -> PersistedRemainderPreview:
    """Consistent preview while holding the instruction lock."""

    _id(company_id, "company_id")
    _id(instruction_id, "instruction_id")

    await _lock_instruction(
        db, company_id, instruction_id
    )

    opening = await _lock_opening(
        db, company_id, instruction_id
    )

    if opening is None:
        raise DeductionPersistenceError(
            "Remainder opening not found"
        )

    return await _preview_from_opening(db, opening)


async def append_persisted_remainder_event(
    *,
    db: AsyncSession,
    company_id: int,
    instruction_id: int,
    event_id: str,
    amount: Decimal,
    actor_id: int,
) -> PersistedLedgerApplication:
    """Append a technical allocation within the caller transaction.

    Locks the parent instruction and opening to serialize updates.

    PostgreSQL additionally enforces global company-scoped event
    uniqueness. An integrity error must abort the caller transaction.
    """

    _id(company_id, "company_id")
    _id(instruction_id, "instruction_id")
    _id(actor_id, "actor_id")

    _event_id(event_id)

    allocated = _money(
        amount, "amount", allow_zero=False
    )

    await _lock_instruction(
        db, company_id, instruction_id
    )

    opening = await _lock_opening(
        db, company_id, instruction_id
    )

    if opening is None:
        raise DeductionPersistenceError(
            "Remainder opening not found"
        )

    # Company-global event IDs cannot be reused for
    # another deduction instruction.
    event_result = await db.execute(
        select(PayrollDeductionRemainderEvent)
        .where(
            PayrollDeductionRemainderEvent.company_id
            == company_id,
            PayrollDeductionRemainderEvent.event_id
            == event_id,
        )
    )

    existing = event_result.scalar_one_or_none()

    if (
        existing is not None
        and existing.remainder_id != opening.id
    ):
        raise DeductionPersistenceError(
            "Event ID belongs to another instruction"
        )

    current = await _preview_from_opening(
        db, opening
    )

    try:
        application = append_deduction_ledger_event(
            ledger=current.ledger,
            event=DeductionLedgerEvent(
                event_id=event_id,
                company_id=company_id,
                instruction_id=instruction_id,
                amount=allocated,
            ),
        )
    except DeductionLedgerError as exc:
        raise DeductionPersistenceError(str(exc)) from exc

    if application.duplicate:
        return PersistedLedgerApplication(
            preview=current,
            applied_now=Decimal("0.00"),
            duplicate=True,
        )

    db.add(
        PayrollDeductionRemainderEvent(
            company_id=company_id,
            remainder_id=opening.id,
            event_id=event_id,
            amount=allocated,
            created_by=actor_id,
            created_at=datetime.now(timezone.utc),
        )
    )

    await db.flush()

    updated = await _preview_from_opening(
        db, opening
    )

    if (
        updated.applied_amount
        != current.applied_amount + allocated
        or updated.remaining_amount
        != current.remaining_amount - allocated
        or updated.event_count
        != current.event_count + 1
    ):
        raise DeductionPersistenceError(
            "Persisted event reconciliation mismatch"
        )

    return PersistedLedgerApplication(
        preview=updated,
        applied_now=allocated,
        duplicate=False,
    )
