from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.journal_entry import (
    JournalEntry,
    JournalEntryStatus,
)
from app.models.journal_entry_line import (
    JournalEntryLine,
)
from app.models.sales_return_cost_restoration_event import (
    SalesReturnCostRestorationEvent,
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
from app.services.sales_return_cost_restoration_accounting_service import (
    SalesReturnCostRestorationAccountingError,
    SalesReturnCostRestorationAccountingPlan,
    create_sales_return_cost_restoration_accounting_plan,
    required_roles_for_sales_return_cost_restoration_plan,
)


ZERO = Decimal("0")


class SalesReturnCostRestorationJournalError(
    Exception
):
    """Base Sales Return COGS-restoration journal error."""


class SalesReturnCostRestorationJournalSourceStateError(
    SalesReturnCostRestorationJournalError
):
    """Cost-restoration event is invalid for the GL action."""


class SalesReturnCostRestorationJournalDuplicateError(
    SalesReturnCostRestorationJournalError
):
    """A journal already exists for the immutable event."""


class SalesReturnCostRestorationJournalNotFoundError(
    SalesReturnCostRestorationJournalError
):
    """Required original cost-restoration journal does not exist."""


def _validate_event_identity(
    event: SalesReturnCostRestorationEvent,
) -> None:
    if (
        event.id is None
        or event.id <= 0
    ):
        raise SalesReturnCostRestorationJournalSourceStateError(
            "Sales Return Cost Restoration event must have "
            "a persistent positive ID"
        )

    if event.company_id <= 0:
        raise SalesReturnCostRestorationJournalSourceStateError(
            "Sales Return Cost Restoration event company_id "
            "must be greater than zero"
        )


def _event_amount(
    event: SalesReturnCostRestorationEvent,
) -> Decimal:
    try:
        amount = Decimal(
            str(
                event.restored_cost_amount
            )
        )
    except Exception as exc:
        raise SalesReturnCostRestorationJournalSourceStateError(
            "restored_cost_amount must be Decimal-compatible"
        ) from exc

    if not amount.is_finite():
        raise SalesReturnCostRestorationJournalSourceStateError(
            "restored_cost_amount must be finite"
        )

    if amount < ZERO:
        raise SalesReturnCostRestorationJournalSourceStateError(
            "restored_cost_amount cannot be negative"
        )

    return amount


async def _build_journal_lines(
    db: AsyncSession,
    *,
    company_id: int,
    plan: SalesReturnCostRestorationAccountingPlan,
    description: str,
) -> list[
    JournalEntryLine
]:
    try:
        accounts = await resolve_company_account_roles(
            db,
            company_id=company_id,
            roles=(
                required_roles_for_sales_return_cost_restoration_plan(
                    plan
                )
            ),
        )
    except AccountingAccountRoleResolutionError as exc:
        raise SalesReturnCostRestorationJournalError(
            str(
                exc
            )
        ) from exc

    lines = []

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
        raise SalesReturnCostRestorationJournalError(
            str(
                exc
            )
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
        raise SalesReturnCostRestorationJournalError(
            str(
                exc
            )
        ) from exc


async def generate_and_post_sales_return_cost_restoration_journal_entry(
    db: AsyncSession,
    *,
    event: SalesReturnCostRestorationEvent,
    created_by: int,
) -> JournalEntry | None:
    """
    Post one original immutable cost-restoration event:

        Dr INVENTORY_GOODS
        Cr GOODS_COGS

    GENERAL 291:
        Dr 281
        Cr 902

    Amount:
        event.restored_cost_amount

    A legitimate zero-cost inventory return creates no GL entry.
    Physical inventory restoration still remains valid.

    Caller owns COMMIT / ROLLBACK.
    """

    if created_by <= 0:
        raise SalesReturnCostRestorationJournalSourceStateError(
            "created_by must be greater than zero"
        )

    _validate_event_identity(
        event
    )

    if event.reversal_of_id is not None:
        raise SalesReturnCostRestorationJournalSourceStateError(
            "A Sales Return Cost Restoration reversal event "
            "cannot generate an original journal entry"
        )

    amount = _event_amount(
        event
    )

    existing_id = (
        await db.execute(
            select(
                JournalEntry.id
            ).where(
                JournalEntry.company_id
                == event.company_id,
                (
                    JournalEntry
                    .sales_return_cost_restoration_event_id
                    == event.id
                ),
                JournalEntry.reversal_of_id.is_(
                    None
                ),
            )
        )
    ).scalar_one_or_none()

    if existing_id is not None:
        raise SalesReturnCostRestorationJournalDuplicateError(
            "Original journal entry already exists for "
            "Sales Return Cost Restoration event"
        )

    if amount == ZERO:
        return None

    try:
        plan = (
            create_sales_return_cost_restoration_accounting_plan(
                amount
            )
        )
    except SalesReturnCostRestorationAccountingError as exc:
        raise SalesReturnCostRestorationJournalError(
            str(
                exc
            )
        ) from exc

    description = (
        "Sales Return COGS restoration event "
        f"{event.id}"
    )

    lines = await _build_journal_lines(
        db,
        company_id=event.company_id,
        plan=plan,
        description=description,
    )

    journal_entry = JournalEntry(
        company_id=event.company_id,
        document_id=None,
        payment_id=None,
        payment_settlement_allocation_id=None,
        tax_recognition_event_id=None,
        sales_recognition_event_id=None,
        vat_advance_bridge_event_id=None,
        input_vat_fulfillment_bridge_event_id=None,
        supplier_advance_clearing_event_id=None,
        customer_advance_clearing_event_id=None,
        sales_return_recognition_event_id=None,
        sales_return_cost_restoration_event_id=(
            event.id
        ),
        accounting_rule_id=None,
        entry_date=event.restoration_date,
        description=description,
        status=JournalEntryStatus.DRAFT,
        created_by=created_by,
    )

    journal_entry.lines = (
        lines
    )

    return await _validate_and_post(
        db,
        journal_entry=journal_entry,
    )


async def get_original_sales_return_cost_restoration_journal_entry(
    db: AsyncSession,
    *,
    company_id: int,
    sales_return_cost_restoration_event_id: int,
    lock: bool = False,
) -> JournalEntry:
    if company_id <= 0:
        raise ValueError(
            "company_id must be greater than zero"
        )

    if (
        sales_return_cost_restoration_event_id
        <= 0
    ):
        raise ValueError(
            "sales_return_cost_restoration_event_id "
            "must be greater than zero"
        )

    statement = (
        select(
            JournalEntry
        )
        .where(
            JournalEntry.company_id
            == company_id,
            (
                JournalEntry
                .sales_return_cost_restoration_event_id
                == (
                    sales_return_cost_restoration_event_id
                )
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
        raise SalesReturnCostRestorationJournalNotFoundError(
            "Original journal entry not found for "
            "Sales Return Cost Restoration event"
        )

    return entry


async def reverse_sales_return_cost_restoration_journal_entry(
    db: AsyncSession,
    *,
    reversal_event: SalesReturnCostRestorationEvent,
    reversed_by: int,
) -> JournalEntry | None:
    """
    Reverse one original cost-restoration GL entry.

    Original:
        Dr INVENTORY_GOODS
        Cr GOODS_COGS

    Reversal:
        Dr GOODS_COGS
        Cr INVENTORY_GOODS

    The reversal JournalEntry is typed by the immutable
    reversal SalesReturnCostRestorationEvent itself.

    Zero-cost events have no original GL entry and therefore
    require no GL reversal.

    Caller owns COMMIT / ROLLBACK.
    """

    if reversed_by <= 0:
        raise SalesReturnCostRestorationJournalSourceStateError(
            "reversed_by must be greater than zero"
        )

    _validate_event_identity(
        reversal_event
    )

    if reversal_event.reversal_of_id is None:
        raise SalesReturnCostRestorationJournalSourceStateError(
            "Only a Sales Return Cost Restoration reversal "
            "event can reverse a journal entry"
        )

    amount = _event_amount(
        reversal_event
    )

    existing_reversal_id = (
        await db.execute(
            select(
                JournalEntry.id
            ).where(
                JournalEntry.company_id
                == reversal_event.company_id,
                (
                    JournalEntry
                    .sales_return_cost_restoration_event_id
                    == reversal_event.id
                ),
            )
        )
    ).scalar_one_or_none()

    if existing_reversal_id is not None:
        raise SalesReturnCostRestorationJournalDuplicateError(
            "Journal entry already exists for "
            "Sales Return Cost Restoration reversal event"
        )

    if amount == ZERO:
        return None

    original_entry = (
        await get_original_sales_return_cost_restoration_journal_entry(
            db,
            company_id=reversal_event.company_id,
            sales_return_cost_restoration_event_id=(
                reversal_event.reversal_of_id
            ),
            lock=True,
        )
    )

    try:
        return await reverse_journal_entry(
            db,
            company_id=reversal_event.company_id,
            journal_entry_id=original_entry.id,
            reversal_date=(
                reversal_event.restoration_date
            ),
            reversed_by=reversed_by,
            sales_return_cost_restoration_event_id_override=(
                reversal_event.id
            ),
        )
    except AccountingReversalError as exc:
        raise SalesReturnCostRestorationJournalError(
            str(
                exc
            )
        ) from exc
