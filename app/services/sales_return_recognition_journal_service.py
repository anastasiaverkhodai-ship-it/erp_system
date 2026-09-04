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
from app.models.sales_return_recognition_event import (
    SalesReturnRecognitionEvent,
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
from app.services.sales_return_recognition_accounting_service import (
    SalesReturnRecognitionAccountingError,
    SalesReturnRecognitionAccountingPlan,
    create_sales_return_recognition_accounting_plan,
    required_roles_for_sales_return_recognition_plan,
)


class SalesReturnRecognitionJournalError(
    Exception
):
    """Base Sales Return Recognition journal error."""


class SalesReturnRecognitionJournalSourceStateError(
    SalesReturnRecognitionJournalError
):
    """Sales Return event is invalid for this GL action."""


class SalesReturnRecognitionJournalDuplicateError(
    SalesReturnRecognitionJournalError
):
    """A JournalEntry already exists for this immutable event."""


class SalesReturnRecognitionJournalNotFoundError(
    SalesReturnRecognitionJournalError
):
    """Required Sales Return JournalEntry does not exist."""


class SalesReturnRecognitionJournalCurrencyError(
    SalesReturnRecognitionJournalError
):
    """Sales Return accounting currency is unsupported."""


def validate_sales_return_recognition_accounting_currency(
    event: SalesReturnRecognitionEvent,
) -> None:
    if event.currency_code != "UAH":
        raise SalesReturnRecognitionJournalCurrencyError(
            "Sales Return Recognition accounting currently "
            "supports UAH only"
        )


def _validate_event_identity(
    event: SalesReturnRecognitionEvent,
) -> None:
    if (
        event.id is None
        or event.id <= 0
    ):
        raise SalesReturnRecognitionJournalSourceStateError(
            "Sales Return Recognition event must have "
            "a persistent positive ID"
        )

    if event.company_id <= 0:
        raise SalesReturnRecognitionJournalSourceStateError(
            "Sales Return Recognition event company_id "
            "must be greater than zero"
        )


async def _build_journal_lines(
    db: AsyncSession,
    *,
    company_id: int,
    plan: SalesReturnRecognitionAccountingPlan,
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
                    required_roles_for_sales_return_recognition_plan(
                        plan
                    )
                ),
            )
        )
    except AccountingAccountRoleResolutionError as exc:
        raise SalesReturnRecognitionJournalError(
            str(
                exc
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
        raise SalesReturnRecognitionJournalError(
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
        raise SalesReturnRecognitionJournalError(
            str(
                exc
            )
        ) from exc


async def generate_and_post_sales_return_recognition_journal_entry(
    db: AsyncSession,
    *,
    event: SalesReturnRecognitionEvent,
    created_by: int,
) -> JournalEntry:
    """
    Post one original immutable SalesReturnRecognitionEvent:

        Dr SALES_DEDUCTIONS
        Cr CUSTOMER_RECEIVABLES

    GENERAL 291 target:

        Dr 704
        Cr 361

    Amount is returned_gross_amount.

    returned_tax_amount is deliberately not posted here.
    Ukrainian VAT / RK remains a separate tax lifecycle.
    """

    if created_by <= 0:
        raise SalesReturnRecognitionJournalSourceStateError(
            "created_by must be greater than zero"
        )

    _validate_event_identity(
        event
    )

    if event.reversal_of_id is not None:
        raise SalesReturnRecognitionJournalSourceStateError(
            "A Sales Return Recognition reversal event "
            "cannot generate an original journal entry"
        )

    validate_sales_return_recognition_accounting_currency(
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
                    .sales_return_recognition_event_id
                    == event.id
                ),
                JournalEntry.reversal_of_id.is_(
                    None
                ),
            )
        )
    ).scalar_one_or_none()

    if existing_id is not None:
        raise SalesReturnRecognitionJournalDuplicateError(
            "Original journal entry already exists "
            "for this Sales Return Recognition event"
        )

    try:
        plan = (
            create_sales_return_recognition_accounting_plan(
                amount=Decimal(
                    str(
                        event.returned_gross_amount
                    )
                )
            )
        )
    except SalesReturnRecognitionAccountingError as exc:
        raise SalesReturnRecognitionJournalError(
            str(
                exc
            )
        ) from exc

    description = (
        "Sales Return Recognition event "
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
        sales_return_recognition_event_id=(
            event.id
        ),
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


async def get_original_sales_return_recognition_journal_entry(
    db: AsyncSession,
    *,
    company_id: int,
    sales_return_recognition_event_id: int,
    lock: bool = False,
) -> JournalEntry:
    if company_id <= 0:
        raise ValueError(
            "company_id must be greater than zero"
        )

    if (
        sales_return_recognition_event_id
        <= 0
    ):
        raise ValueError(
            "sales_return_recognition_event_id "
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
                .sales_return_recognition_event_id
                == sales_return_recognition_event_id
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
        raise SalesReturnRecognitionJournalNotFoundError(
            "Original journal entry not found "
            "for Sales Return Recognition event"
        )

    return entry


async def reverse_sales_return_recognition_journal_entry(
    db: AsyncSession,
    *,
    reversal_event: SalesReturnRecognitionEvent,
    reversed_by: int,
) -> JournalEntry:
    """
    Account for an immutable SalesReturnRecognitionEvent reversal.

    Original accounting:
        Dr 704 / Cr 361

    Generic JournalEntry reversal therefore produces:
        Dr 361 / Cr 704

    The reversal JournalEntry is bound to the immutable reversal
    SalesReturnRecognitionEvent itself through the typed-source
    override, while JournalEntry.reversal_of_id preserves the GL
    relationship to the original JournalEntry.
    """

    if reversed_by <= 0:
        raise SalesReturnRecognitionJournalSourceStateError(
            "reversed_by must be greater than zero"
        )

    _validate_event_identity(
        reversal_event
    )

    if reversal_event.reversal_of_id is None:
        raise SalesReturnRecognitionJournalSourceStateError(
            "Only a Sales Return Recognition reversal event "
            "can reverse Sales Return Recognition accounting"
        )

    if reversal_event.reversal_of_id <= 0:
        raise SalesReturnRecognitionJournalSourceStateError(
            "Sales Return Recognition reversal_of_id "
            "must be greater than zero"
        )

    validate_sales_return_recognition_accounting_currency(
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
                    .sales_return_recognition_event_id
                    == reversal_event.id
                ),
            )
        )
    ).scalar_one_or_none()

    if existing_reversal_id is not None:
        raise SalesReturnRecognitionJournalDuplicateError(
            "Journal entry already exists for this "
            "Sales Return Recognition reversal event"
        )

    original = (
        await get_original_sales_return_recognition_journal_entry(
            db,
            company_id=reversal_event.company_id,
            sales_return_recognition_event_id=(
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
            sales_return_recognition_event_id_override=(
                reversal_event.id
            ),
        )
    except AccountingReversalError as exc:
        raise SalesReturnRecognitionJournalError(
            str(
                exc
            )
        ) from exc
