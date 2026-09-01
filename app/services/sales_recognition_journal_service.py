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
from app.models.sales_recognition_event import (
    SalesRecognitionEvent,
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
from app.services.sales_recognition_accounting_service import (
    SalesRecognitionAccountingError,
    SalesRecognitionAccountingPlan,
    create_sales_recognition_accounting_plan,
    required_roles_for_sales_recognition_plan,
)


class SalesRecognitionJournalError(Exception):
    """Base Sales Recognition journal error."""


class SalesRecognitionJournalSourceStateError(
    SalesRecognitionJournalError
):
    """Sales Recognition event is not valid for this GL action."""


class SalesRecognitionJournalDuplicateError(
    SalesRecognitionJournalError
):
    """A journal entry already exists for this immutable event."""


class SalesRecognitionJournalNotFoundError(
    SalesRecognitionJournalError
):
    """Required Sales Recognition journal entry does not exist."""


class SalesRecognitionJournalCurrencyError(
    SalesRecognitionJournalError
):
    """Sales Recognition accounting currency is unsupported."""


def validate_sales_recognition_accounting_currency(
    event: SalesRecognitionEvent,
) -> None:
    if event.currency_code != "UAH":
        raise SalesRecognitionJournalCurrencyError(
            "Sales Recognition accounting currently "
            "supports UAH only"
        )


def _validate_event_identity(
    event: SalesRecognitionEvent,
) -> None:
    if event.id is None or event.id <= 0:
        raise SalesRecognitionJournalSourceStateError(
            "Sales Recognition event must have "
            "a persistent positive ID"
        )

    if event.company_id <= 0:
        raise SalesRecognitionJournalSourceStateError(
            "Sales Recognition event company_id "
            "must be greater than zero"
        )


async def _build_journal_lines(
    db: AsyncSession,
    *,
    company_id: int,
    plan: SalesRecognitionAccountingPlan,
    description: str,
) -> list[JournalEntryLine]:
    try:
        accounts = await resolve_company_account_roles(
            db,
            company_id=company_id,
            roles=(
                required_roles_for_sales_recognition_plan(
                    plan
                )
            ),
        )
    except AccountingAccountRoleResolutionError as exc:
        raise SalesRecognitionJournalError(
            str(exc)
        ) from exc

    lines: list[JournalEntryLine] = []

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
        raise SalesRecognitionJournalError(
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
        raise SalesRecognitionJournalError(
            str(exc)
        ) from exc


async def generate_and_post_sales_recognition_journal_entry(
    db: AsyncSession,
    *,
    event: SalesRecognitionEvent,
    created_by: int,
) -> JournalEntry:
    """
    Post one original immutable SalesRecognitionEvent:

        Dr CUSTOMER_RECEIVABLES
        Cr GOODS_REVENUE

    Amount is recognized_gross_amount.

    VAT remains outside this journal plan.
    """

    if created_by <= 0:
        raise SalesRecognitionJournalSourceStateError(
            "created_by must be greater than zero"
        )

    _validate_event_identity(
        event
    )

    if event.reversal_of_id is not None:
        raise SalesRecognitionJournalSourceStateError(
            "A Sales Recognition reversal event "
            "cannot generate an original journal entry"
        )

    validate_sales_recognition_accounting_currency(
        event
    )

    existing_id = (
        await db.execute(
            select(
                JournalEntry.id
            ).where(
                JournalEntry.company_id
                == event.company_id,
                JournalEntry.sales_recognition_event_id
                == event.id,
                JournalEntry.reversal_of_id.is_(
                    None
                ),
            )
        )
    ).scalar_one_or_none()

    if existing_id is not None:
        raise SalesRecognitionJournalDuplicateError(
            "Original journal entry already exists "
            "for this Sales Recognition event"
        )

    try:
        plan = (
            create_sales_recognition_accounting_plan(
                amount=Decimal(
                    str(
                        event.recognized_gross_amount
                    )
                ),
            )
        )
    except SalesRecognitionAccountingError as exc:
        raise SalesRecognitionJournalError(
            str(exc)
        ) from exc

    description = (
        "Sales Recognition event "
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
        sales_recognition_event_id=event.id,
        accounting_rule_id=None,
        entry_date=event.recognition_date,
        description=description,
        status=JournalEntryStatus.DRAFT,
        created_by=created_by,
    )

    journal_entry.lines = lines

    return await _validate_and_post(
        db,
        journal_entry=journal_entry,
    )


async def get_original_sales_recognition_journal_entry(
    db: AsyncSession,
    *,
    company_id: int,
    sales_recognition_event_id: int,
    lock: bool = False,
) -> JournalEntry:
    if company_id <= 0:
        raise ValueError(
            "company_id must be greater than zero"
        )

    if sales_recognition_event_id <= 0:
        raise ValueError(
            "sales_recognition_event_id must be "
            "greater than zero"
        )

    statement = (
        select(
            JournalEntry
        )
        .where(
            JournalEntry.company_id
            == company_id,
            JournalEntry.sales_recognition_event_id
            == sales_recognition_event_id,
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
        raise SalesRecognitionJournalNotFoundError(
            "Original journal entry not found "
            "for Sales Recognition event"
        )

    return entry


async def reverse_sales_recognition_journal_entry(
    db: AsyncSession,
    *,
    reversal_event: SalesRecognitionEvent,
    reversed_by: int,
) -> JournalEntry:
    """
    Account for an immutable SalesRecognitionEvent reversal.

    The accounting reversal points to the reversal event itself,
    while reversal_of_id on JournalEntry preserves the GL link
    to the original JournalEntry.
    """

    if reversed_by <= 0:
        raise SalesRecognitionJournalSourceStateError(
            "reversed_by must be greater than zero"
        )

    _validate_event_identity(
        reversal_event
    )

    if reversal_event.reversal_of_id is None:
        raise SalesRecognitionJournalSourceStateError(
            "Only a Sales Recognition reversal event "
            "can reverse Sales Recognition accounting"
        )

    if reversal_event.reversal_of_id <= 0:
        raise SalesRecognitionJournalSourceStateError(
            "Sales Recognition reversal_of_id "
            "must be greater than zero"
        )

    validate_sales_recognition_accounting_currency(
        reversal_event
    )

    existing_reversal_id = (
        await db.execute(
            select(
                JournalEntry.id
            ).where(
                JournalEntry.company_id
                == reversal_event.company_id,
                JournalEntry.sales_recognition_event_id
                == reversal_event.id,
            )
        )
    ).scalar_one_or_none()

    if existing_reversal_id is not None:
        raise SalesRecognitionJournalDuplicateError(
            "Journal entry already exists for this "
            "Sales Recognition reversal event"
        )

    original = (
        await get_original_sales_recognition_journal_entry(
            db,
            company_id=reversal_event.company_id,
            sales_recognition_event_id=(
                reversal_event.reversal_of_id
            ),
            lock=True,
        )
    )

    try:
        return await reverse_journal_entry(
            db=db,
            company_id=reversal_event.company_id,
            journal_entry_id=original.id,
            reversal_date=(
                reversal_event.recognition_date
            ),
            reversed_by=reversed_by,
            sales_recognition_event_id_override=(
                reversal_event.id
            ),
        )
    except AccountingReversalError as exc:
        raise SalesRecognitionJournalError(
            str(exc)
        ) from exc
