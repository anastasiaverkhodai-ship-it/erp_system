from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.journal_entry import (
    JournalEntry,
    JournalEntryStatus,
)
from app.models.journal_entry_line import (
    JournalEntryLine,
)
from app.models.payment import Payment
from app.models.payment_settlement_allocation import (
    PaymentSettlementAllocation,
)
from app.services.accounting_account_role_resolver import (
    AccountingAccountRoleResolutionError,
    resolve_company_account_roles,
)
from app.services.accounting_posting import (
    AccountingPostingError,
    post_journal_entry,
    validate_journal_entry,
)
from app.services.accounting_reversal import (
    AccountingReversalError,
    reverse_journal_entry,
)
from app.services.payment_accounting_service import (
    PaymentAccountingError,
    PaymentAccountingPlan,
    create_payment_confirmation_accounting_plan,
    create_settlement_accounting_plan,
    required_roles_for_plan,
)
from app.services.payment_types import (
    PaymentSettlementAllocationStatus,
    PaymentStatus,
)


class PaymentJournalError(Exception):
    pass


class PaymentJournalSourceStateError(
    PaymentJournalError
):
    pass


class PaymentJournalDuplicateError(
    PaymentJournalError
):
    pass


class PaymentJournalNotFoundError(
    PaymentJournalError
):
    pass


class PaymentJournalCurrencyError(
    PaymentJournalError
):
    pass


def validate_payment_accounting_currency(
    payment: Payment,
) -> None:
    if payment.currency_code != "UAH":
        raise PaymentJournalCurrencyError(
            "Payment accounting currently supports "
            "UAH only"
        )


async def _build_journal_lines(
    db: AsyncSession,
    *,
    company_id: int,
    plan: PaymentAccountingPlan,
    description: str,
) -> list[JournalEntryLine]:
    try:
        accounts = await resolve_company_account_roles(
            db,
            company_id=company_id,
            roles=required_roles_for_plan(
                plan
            ),
        )
    except AccountingAccountRoleResolutionError as exc:
        raise PaymentJournalError(
            str(exc)
        ) from exc

    lines: list[
        JournalEntryLine
    ] = []

    for line_no, planned in enumerate(
        plan.lines,
        start=1,
    ):
        account = accounts[
            planned.role
        ]

        lines.append(
            JournalEntryLine(
                line_no=line_no,
                account_id=account.id,
                debit=planned.debit,
                credit=planned.credit,
                description=description,
            )
        )

    return lines


async def _validate_and_post(
    db: AsyncSession,
    *,
    journal_entry: JournalEntry,
) -> JournalEntry:
    try:
        await validate_journal_entry(
            db=db,
            journal_entry=journal_entry,
        )
    except AccountingPostingError as exc:
        raise PaymentJournalError(
            str(exc)
        ) from exc

    db.add(
        journal_entry
    )

    await db.flush()

    try:
        return await post_journal_entry(
            db=db,
            company_id=journal_entry.company_id,
            journal_entry_id=journal_entry.id,
        )
    except AccountingPostingError as exc:
        raise PaymentJournalError(
            str(exc)
        ) from exc


async def generate_and_post_payment_journal_entry(
    db: AsyncSession,
    *,
    payment: Payment,
    created_by: int,
) -> JournalEntry:
    if created_by <= 0:
        raise PaymentJournalSourceStateError(
            "created_by must be greater than zero"
        )

    if payment.status != PaymentStatus.CONFIRMED:
        raise PaymentJournalSourceStateError(
            "Only CONFIRMED Payment can generate "
            "a journal entry"
        )

    validate_payment_accounting_currency(
        payment
    )

    existing_id = (
        await db.execute(
            select(
                JournalEntry.id
            ).where(
                JournalEntry.company_id
                == payment.company_id,
                JournalEntry.payment_id
                == payment.id,
                JournalEntry.reversal_of_id.is_(
                    None
                ),
            )
        )
    ).scalar_one_or_none()

    if existing_id is not None:
        raise PaymentJournalDuplicateError(
            "Original journal entry already exists "
            "for this Payment"
        )

    try:
        plan = (
            create_payment_confirmation_accounting_plan(
                direction=payment.direction,
                amount=Decimal(
                    payment.amount
                ),
            )
        )
    except PaymentAccountingError as exc:
        raise PaymentJournalError(
            str(exc)
        ) from exc

    description = (
        f"Payment {payment.number}"
    )

    lines = await _build_journal_lines(
        db,
        company_id=payment.company_id,
        plan=plan,
        description=description,
    )

    journal_entry = JournalEntry(
        company_id=payment.company_id,
        document_id=None,
        payment_id=payment.id,
        payment_settlement_allocation_id=None,
        accounting_rule_id=None,
        entry_date=payment.payment_date,
        description=description,
        status=JournalEntryStatus.DRAFT,
        created_by=created_by,
    )

    journal_entry.lines = lines

    return await _validate_and_post(
        db,
        journal_entry=journal_entry,
    )


async def generate_and_post_settlement_journal_entry(
    db: AsyncSession,
    *,
    payment: Payment,
    allocation: PaymentSettlementAllocation,
    created_by: int,
) -> JournalEntry:
    if created_by <= 0:
        raise PaymentJournalSourceStateError(
            "created_by must be greater than zero"
        )

    if payment.status != PaymentStatus.CONFIRMED:
        raise PaymentJournalSourceStateError(
            "Settlement accounting requires "
            "CONFIRMED Payment"
        )

    if (
        allocation.status
        != PaymentSettlementAllocationStatus.ACTIVE
    ):
        raise PaymentJournalSourceStateError(
            "Only ACTIVE settlement allocation "
            "can generate a journal entry"
        )

    if (
        allocation.company_id
        != payment.company_id
        or allocation.payment_id
        != payment.id
    ):
        raise PaymentJournalSourceStateError(
            "Settlement allocation does not belong "
            "to the Payment"
        )

    validate_payment_accounting_currency(
        payment
    )

    existing_id = (
        await db.execute(
            select(
                JournalEntry.id
            ).where(
                JournalEntry.company_id
                == allocation.company_id,
                (
                    JournalEntry
                    .payment_settlement_allocation_id
                    == allocation.id
                ),
                JournalEntry.reversal_of_id.is_(
                    None
                ),
            )
        )
    ).scalar_one_or_none()

    if existing_id is not None:
        raise PaymentJournalDuplicateError(
            "Original journal entry already exists "
            "for this settlement allocation"
        )

    try:
        plan = create_settlement_accounting_plan(
            direction=payment.direction,
            amount=Decimal(
                allocation.amount
            ),
        )
    except PaymentAccountingError as exc:
        raise PaymentJournalError(
            str(exc)
        ) from exc

    description = (
        "Settlement allocation "
        f"{allocation.id} for Payment "
        f"{payment.number}"
    )

    lines = await _build_journal_lines(
        db,
        company_id=payment.company_id,
        plan=plan,
        description=description,
    )

    accounting_date = (
        allocation.created_at.date()
        if allocation.created_at is not None
        else date.today()
    )

    journal_entry = JournalEntry(
        company_id=payment.company_id,
        document_id=None,
        payment_id=None,
        payment_settlement_allocation_id=(
            allocation.id
        ),
        accounting_rule_id=None,
        entry_date=accounting_date,
        description=description,
        status=JournalEntryStatus.DRAFT,
        created_by=created_by,
    )

    journal_entry.lines = lines

    return await _validate_and_post(
        db,
        journal_entry=journal_entry,
    )


async def get_original_payment_journal_entry(
    db: AsyncSession,
    *,
    company_id: int,
    payment_id: int,
    lock: bool = False,
) -> JournalEntry:
    statement = (
        select(
            JournalEntry
        )
        .options(
            selectinload(
                JournalEntry.lines
            )
        )
        .where(
            JournalEntry.company_id
            == company_id,
            JournalEntry.payment_id
            == payment_id,
            JournalEntry.reversal_of_id.is_(
                None
            ),
        )
    )

    if lock:
        statement = (
            statement.with_for_update()
        )

    entry = (
        await db.execute(
            statement
        )
    ).scalar_one_or_none()

    if entry is None:
        raise PaymentJournalNotFoundError(
            "Payment journal entry not found"
        )

    return entry


async def get_original_settlement_journal_entry(
    db: AsyncSession,
    *,
    company_id: int,
    allocation_id: int,
    lock: bool = False,
) -> JournalEntry:
    statement = (
        select(
            JournalEntry
        )
        .options(
            selectinload(
                JournalEntry.lines
            )
        )
        .where(
            JournalEntry.company_id
            == company_id,
            (
                JournalEntry
                .payment_settlement_allocation_id
                == allocation_id
            ),
            JournalEntry.reversal_of_id.is_(
                None
            ),
        )
    )

    if lock:
        statement = (
            statement.with_for_update()
        )

    entry = (
        await db.execute(
            statement
        )
    ).scalar_one_or_none()

    if entry is None:
        raise PaymentJournalNotFoundError(
            "Settlement journal entry not found"
        )

    return entry


async def reverse_payment_journal_entry(
    db: AsyncSession,
    *,
    company_id: int,
    payment_id: int,
    reversal_date: date,
    reversed_by: int,
) -> JournalEntry:
    original = (
        await get_original_payment_journal_entry(
            db,
            company_id=company_id,
            payment_id=payment_id,
            lock=True,
        )
    )

    try:
        return await reverse_journal_entry(
            db=db,
            company_id=company_id,
            journal_entry_id=original.id,
            reversal_date=reversal_date,
            reversed_by=reversed_by,
        )
    except AccountingReversalError as exc:
        raise PaymentJournalError(
            str(exc)
        ) from exc


async def reverse_settlement_journal_entry(
    db: AsyncSession,
    *,
    company_id: int,
    allocation_id: int,
    reversal_date: date,
    reversed_by: int,
) -> JournalEntry:
    original = (
        await get_original_settlement_journal_entry(
            db,
            company_id=company_id,
            allocation_id=allocation_id,
            lock=True,
        )
    )

    try:
        return await reverse_journal_entry(
            db=db,
            company_id=company_id,
            journal_entry_id=original.id,
            reversal_date=reversal_date,
            reversed_by=reversed_by,
        )
    except AccountingReversalError as exc:
        raise PaymentJournalError(
            str(exc)
        ) from exc
