"""Technical payroll deduction remittance persistence.

Phase 13 C4-C1.

This service stores candidate allocations only.

It does not:
- authorize a deduction;
- prove withheld funds are available;
- execute bank transfers;
- create accounting entries;
- post payroll;
- commit or rollback database transactions.

The caller must own an already active transaction.
"""

from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.payroll_deduction import (
    PayrollDeductionInstruction,
)
from app.models.payroll_deduction_remittance import (
    PayrollDeductionRemittancePlan,
    PayrollDeductionRemittanceEvent,
)
from app.services.payroll_deduction_remittance_rules import (
    RemittanceEvent,
    RemittancePlan,
    RemittanceSource,
    RemittanceRuleError,
    append_remittance_event,
    reconcile_remittance_plan,
)


class RemittancePersistenceError(ValueError):
    """Persistence or source identity violation."""


def _id(value, name):
    if type(value) is not int or value <= 0:
        raise RemittancePersistenceError(
            f"{name} must be a positive integer"
        )


def _transaction(db):
    if not isinstance(db, AsyncSession):
        raise RemittancePersistenceError(
            "AsyncSession required"
        )

    if not db.in_transaction():
        raise RemittancePersistenceError(
            "Caller-owned transaction required"
        )


async def _lock(db, company_id, instruction_id):
    """Serialize writers of the same company/instruction.

    Transaction-scoped PostgreSQL advisory lock.
    No service commit or rollback.
    """

    _id(company_id, "company_id")
    _id(instruction_id, "instruction_id")

    # PostgreSQL's two-integer advisory lock uses int32 keys.
    limit = 2**31 - 1

    if company_id > limit or instruction_id > limit:
        raise RemittancePersistenceError(
            "Advisory lock identifiers exceed int32 range"
        )

    await db.execute(
        text("""
            SELECT pg_advisory_xact_lock(
                :company_id,
                :instruction_id
            )
        """),
        {
            "company_id": company_id,
            "instruction_id": instruction_id,
        },
    )


async def _source_instruction(
    db, company_id, instruction_id
):
    statement = (
        select(PayrollDeductionInstruction)
        .where(
            PayrollDeductionInstruction.company_id == company_id,
            PayrollDeductionInstruction.id == instruction_id,
        )
        .with_for_update()
    )

    instruction = (
        await db.execute(statement)
    ).scalar_one_or_none()

    if instruction is None:
        raise RemittancePersistenceError(
            "Deduction instruction not found in company"
        )

    return instruction


def _plan_source(row):
    return RemittanceSource(
        company_id=row.company_id,
        instruction_id=row.instruction_id,
        beneficiary_reference=row.beneficiary_reference,
        currency_code=row.currency_code,
        available_amount=row.available_amount,
    )


async def _read_plan(db, company_id, instruction_id):
    statement = (
        select(PayrollDeductionRemittancePlan)
        .where(
            PayrollDeductionRemittancePlan.company_id == company_id,
            PayrollDeductionRemittancePlan.instruction_id
            == instruction_id,
        )
        .with_for_update()
    )

    return (
        await db.execute(statement)
    ).scalar_one_or_none()


async def _read_history(db, plan_row):
    """Rebuild the full immutable technical history."""

    statement = (
        select(PayrollDeductionRemittanceEvent)
        .where(
            PayrollDeductionRemittanceEvent.company_id
            == plan_row.company_id,
            PayrollDeductionRemittanceEvent.plan_id
            == plan_row.id,
        )
        .order_by(
            PayrollDeductionRemittanceEvent.id
        )
    )

    rows = (
        await db.execute(statement)
    ).scalars().all()

    events = tuple(
        RemittanceEvent(
            event_id=row.event_id,
            company_id=row.company_id,
            instruction_id=plan_row.instruction_id,
            beneficiary_reference=plan_row.beneficiary_reference,
            currency_code=plan_row.currency_code,
            amount=row.amount,
        )
        for row in rows
    )

    return RemittancePlan(
        source=_plan_source(plan_row),
        events=events,
    )


async def open_persisted_remittance_plan(
    *,
    db: AsyncSession,
    company_id: int,
    instruction_id: int,
    beneficiary_reference: str,
    currency_code: str,
    available_amount: Decimal,
    created_by: int,
):
    """Open or exactly replay a technical remittance plan.

    Source amount is a technical ceiling, not legal authorization
    or confirmation of money actually withheld.
    """

    _transaction(db)
    _id(company_id, "company_id")
    _id(instruction_id, "instruction_id")
    _id(created_by, "created_by")

    # Pure-rule validation before database work.
    requested = RemittanceSource(
        company_id=company_id,
        instruction_id=instruction_id,
        beneficiary_reference=beneficiary_reference,
        currency_code=currency_code,
        available_amount=available_amount,
    )

    await _lock(db, company_id, instruction_id)

    instruction = await _source_instruction(
        db, company_id, instruction_id
    )

    if instruction.currency_code != currency_code:
        raise RemittancePersistenceError(
            "Instruction currency mismatch"
        )

    existing = await _read_plan(
        db, company_id, instruction_id
    )

    if existing is not None:
        if _plan_source(existing) != requested:
            raise RemittancePersistenceError(
                "Changed remittance opening replay"
            )

        ledger = await _read_history(db, existing)

        return (
            existing,
            reconcile_remittance_plan(ledger),
            True,
        )

    row = PayrollDeductionRemittancePlan(
        company_id=company_id,
        instruction_id=instruction_id,
        beneficiary_reference=beneficiary_reference,
        currency_code=currency_code,
        available_amount=available_amount,
        created_by=created_by,
    )

    db.add(row)

    # Flush is owned by the caller's transaction.
    await db.flush()

    ledger = await _read_history(db, row)

    return (
        row,
        reconcile_remittance_plan(ledger),
        False,
    )


async def append_persisted_remittance_event(
    *,
    db: AsyncSession,
    company_id: int,
    instruction_id: int,
    event_id: str,
    amount: Decimal,
    created_by: int,
):
    """Append a technical allocation with strict idempotency.

    Returns the allocation application from the pure rule engine.

    This function does not authorize a payment.
    """

    _transaction(db)
    _id(company_id, "company_id")
    _id(instruction_id, "instruction_id")
    _id(created_by, "created_by")

    await _lock(db, company_id, instruction_id)

    plan_row = await _read_plan(
        db, company_id, instruction_id
    )

    if plan_row is None:
        raise RemittancePersistenceError(
            "Remittance plan not found in company"
        )

    ledger = await _read_history(db, plan_row)

    candidate = RemittanceEvent(
        event_id=event_id,
        company_id=company_id,
        instruction_id=instruction_id,
        beneficiary_reference=plan_row.beneficiary_reference,
        currency_code=plan_row.currency_code,
        amount=amount,
    )

    # The unique event identity is company-scoped across plans.
    # Detect a collision before inserting any row.
    collision = (
        await db.execute(
            select(PayrollDeductionRemittanceEvent)
            .where(
                PayrollDeductionRemittanceEvent.company_id
                == company_id,
                PayrollDeductionRemittanceEvent.event_id
                == event_id,
            )
        )
    ).scalar_one_or_none()

    if collision is not None and collision.plan_id != plan_row.id:
        raise RemittancePersistenceError(
            "Event identity already belongs to another plan"
        )

    # Pure engine enforces identity, exact replay and ceiling.
    application = append_remittance_event(
        plan=ledger,
        event=candidate,
    )

    if application.duplicate:
        return application

    row = PayrollDeductionRemittanceEvent(
        company_id=company_id,
        plan_id=plan_row.id,
        event_id=event_id,
        amount=amount,
        created_by=created_by,
    )

    db.add(row)
    await db.flush()

    # Independently reconstruct persisted rows after flushing.
    persisted = await _read_history(db, plan_row)
    persisted_balance = reconcile_remittance_plan(persisted)

    if persisted_balance != application.balance:
        raise RemittancePersistenceError(
            "Persisted remittance balance mismatch"
        )

    return application


async def read_persisted_remittance_balance(
    *,
    db: AsyncSession,
    company_id: int,
    instruction_id: int,
):
    """Read/reconcile a technical plan, without authorization."""

    _transaction(db)
    _id(company_id, "company_id")
    _id(instruction_id, "instruction_id")

    row = await _read_plan(
        db, company_id, instruction_id
    )

    if row is None:
        raise RemittancePersistenceError(
            "Remittance plan not found in company"
        )

    ledger = await _read_history(db, row)
    return reconcile_remittance_plan(ledger)
