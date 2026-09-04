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
from app.models.customer_advance_clearing_event import (
    CustomerAdvanceClearingEvent,
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
from app.services.customer_advance_clearing_accounting_service import (
    CustomerAdvanceClearingAccountingError,
    CustomerAdvanceClearingAccountingPlan,
    create_customer_advance_clearing_accounting_plan,
    required_roles_for_customer_advance_clearing_plan,
)


ZERO = Decimal("0")


class CustomerAdvanceClearingJournalError(
    Exception
):
    """Base customer advance clearing journal error."""


class CustomerAdvanceClearingJournalSourceStateError(
    CustomerAdvanceClearingJournalError
):
    """Supplier clearing event is invalid for this GL action."""


class CustomerAdvanceClearingJournalDuplicateError(
    CustomerAdvanceClearingJournalError
):
    """A journal already exists for this immutable event."""


class CustomerAdvanceClearingJournalNotFoundError(
    CustomerAdvanceClearingJournalError
):
    """Required supplier clearing journal does not exist."""


class CustomerAdvanceClearingJournalCurrencyError(
    CustomerAdvanceClearingJournalError
):
    """Supplier clearing accounting currency is unsupported."""


def validate_customer_advance_clearing_accounting_currency(
    event: CustomerAdvanceClearingEvent,
) -> None:
    if event.currency_code != "UAH":
        raise (
            CustomerAdvanceClearingJournalCurrencyError(
                "Customer advance clearing accounting "
                "currently supports UAH only"
            )
        )


def _validate_event_identity(
    event: CustomerAdvanceClearingEvent,
) -> None:
    if (
        event.id is None
        or event.id <= 0
    ):
        raise (
            CustomerAdvanceClearingJournalSourceStateError(
                "Customer advance clearing event must "
                "have a persistent positive ID"
            )
        )

    if event.company_id <= 0:
        raise (
            CustomerAdvanceClearingJournalSourceStateError(
                "Customer advance clearing event "
                "company_id must be greater than zero"
            )
        )


def _event_amount(
    event: CustomerAdvanceClearingEvent,
) -> Decimal:
    try:
        amount = Decimal(
            str(
                event.cleared_amount
            )
        )
    except Exception as exc:
        raise (
            CustomerAdvanceClearingJournalSourceStateError(
                "Customer advance clearing amount "
                "must be a valid Decimal"
            )
        ) from exc

    if not amount.is_finite():
        raise (
            CustomerAdvanceClearingJournalSourceStateError(
                "Customer advance clearing amount "
                "must be finite"
            )
        )

    if amount <= ZERO:
        raise (
            CustomerAdvanceClearingJournalSourceStateError(
                "Customer advance clearing amount "
                "must be greater than zero"
            )
        )

    return amount


async def _build_journal_lines(
    db: AsyncSession,
    *,
    company_id: int,
    plan: CustomerAdvanceClearingAccountingPlan,
    description: str,
) -> list[
    JournalEntryLine
]:
    try:
        accounts = (
            await resolve_company_account_roles(
                db,
                company_id=company_id,
                roles=(
                    required_roles_for_customer_advance_clearing_plan(
                        plan
                    )
                ),
            )
        )
    except (
        AccountingAccountRoleResolutionError
    ) as exc:
        raise (
            CustomerAdvanceClearingJournalError(
                str(
                    exc
                )
            )
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
        raise (
            CustomerAdvanceClearingJournalError(
                str(
                    exc
                )
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
        raise (
            CustomerAdvanceClearingJournalError(
                str(
                    exc
                )
            )
        ) from exc


async def generate_and_post_customer_advance_clearing_journal_entry(
    db: AsyncSession,
    *,
    event: CustomerAdvanceClearingEvent,
    created_by: int,
) -> JournalEntry:
    """
    Post one original immutable supplier clearing event.

    Original:
        Dr CUSTOMER_ADVANCES
        Cr CUSTOMER_RECEIVABLES

    GENERAL 291:
        Dr 631
        Cr 371

    The journal entry is bound to
    CustomerAdvanceClearingEvent.id.
    """

    if created_by <= 0:
        raise (
            CustomerAdvanceClearingJournalSourceStateError(
                "created_by must be greater than zero"
            )
        )

    _validate_event_identity(
        event
    )

    if event.reversal_of_id is not None:
        raise (
            CustomerAdvanceClearingJournalSourceStateError(
                "A Customer Advance Clearing reversal "
                "event cannot generate an original "
                "journal entry"
            )
        )

    validate_customer_advance_clearing_accounting_currency(
        event
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
                    .customer_advance_clearing_event_id
                    == event.id
                ),
                JournalEntry.reversal_of_id.is_(
                    None
                ),
            )
        )
    ).scalar_one_or_none()

    if existing_id is not None:
        raise (
            CustomerAdvanceClearingJournalDuplicateError(
                "Original journal entry already exists "
                "for this Customer Advance Clearing event"
            )
        )

    try:
        plan = (
            create_customer_advance_clearing_accounting_plan(
                amount=amount,
            )
        )
    except (
        CustomerAdvanceClearingAccountingError
    ) as exc:
        raise (
            CustomerAdvanceClearingJournalError(
                str(
                    exc
                )
            )
        ) from exc

    description = (
        "Customer Advance Clearing event "
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
        customer_advance_clearing_event_id=(
            event.id
        ),
        accounting_rule_id=None,
        entry_date=event.clearing_date,
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


async def get_original_customer_advance_clearing_journal_entry(
    db: AsyncSession,
    *,
    company_id: int,
    customer_advance_clearing_event_id: int,
    lock: bool = False,
) -> JournalEntry:
    if company_id <= 0:
        raise ValueError(
            "company_id must be greater than zero"
        )

    if (
        customer_advance_clearing_event_id
        <= 0
    ):
        raise ValueError(
            "customer_advance_clearing_event_id "
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
                .customer_advance_clearing_event_id
                == customer_advance_clearing_event_id
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
        raise (
            CustomerAdvanceClearingJournalNotFoundError(
                "Original journal entry not found "
                "for Customer Advance Clearing event"
            )
        )

    return entry


async def reverse_customer_advance_clearing_journal_entry(
    db: AsyncSession,
    *,
    reversal_event: CustomerAdvanceClearingEvent,
    reversed_by: int,
) -> JournalEntry:
    """
    Account for an immutable supplier clearing reversal.

    Original:
        Dr CUSTOMER_ADVANCES
        Cr CUSTOMER_RECEIVABLES

        Dr 681 / Cr 361

    Generic JournalEntry reversal:
        Dr CUSTOMER_RECEIVABLES
        Cr CUSTOMER_ADVANCES

        Dr 361 / Cr 681

    The reversal JournalEntry is bound to the immutable
    reversal CustomerAdvanceClearingEvent.
    """

    if reversed_by <= 0:
        raise (
            CustomerAdvanceClearingJournalSourceStateError(
                "reversed_by must be greater than zero"
            )
        )

    _validate_event_identity(
        reversal_event
    )

    if (
        reversal_event.reversal_of_id
        is None
    ):
        raise (
            CustomerAdvanceClearingJournalSourceStateError(
                "Only a Customer Advance Clearing "
                "reversal event can reverse supplier "
                "advance clearing accounting"
            )
        )

    if (
        reversal_event.reversal_of_id
        <= 0
    ):
        raise (
            CustomerAdvanceClearingJournalSourceStateError(
                "Customer Advance Clearing "
                "reversal_of_id must be greater than zero"
            )
        )

    validate_customer_advance_clearing_accounting_currency(
        reversal_event
    )

    _event_amount(
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
                    .customer_advance_clearing_event_id
                    == reversal_event.id
                ),
            )
        )
    ).scalar_one_or_none()

    if (
        existing_reversal_id
        is not None
    ):
        raise (
            CustomerAdvanceClearingJournalDuplicateError(
                "Journal entry already exists for this "
                "Customer Advance Clearing reversal event"
            )
        )

    original = (
        await get_original_customer_advance_clearing_journal_entry(
            db,
            company_id=(
                reversal_event.company_id
            ),
            customer_advance_clearing_event_id=(
                reversal_event.reversal_of_id
            ),
            lock=True,
        )
    )

    try:
        return await reverse_journal_entry(
            db=db,
            company_id=(
                reversal_event.company_id
            ),
            journal_entry_id=(
                original.id
            ),
            reversal_date=(
                reversal_event.clearing_date
            ),
            reversed_by=reversed_by,
            customer_advance_clearing_event_id_override=(
                reversal_event.id
            ),
        )
    except AccountingReversalError as exc:
        raise (
            CustomerAdvanceClearingJournalError(
                str(
                    exc
                )
            )
        ) from exc
