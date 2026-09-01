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
from app.models.tax_recognition_event import (
    TaxRecognitionEvent,
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
from app.services.tax_recognition_accounting_service import (
    OutputVatRecognitionSourceKind,
    TaxRecognitionAccountingError,
    TaxRecognitionAccountingPlan,
    create_output_vat_recognition_accounting_plan,
    required_roles_for_output_vat_plan,
)


ZERO = Decimal("0")


class TaxRecognitionJournalError(Exception):
    """Base OUTPUT VAT recognition journal error."""


class TaxRecognitionJournalSourceStateError(
    TaxRecognitionJournalError
):
    """Tax recognition event is invalid for this GL action."""


class TaxRecognitionJournalDuplicateError(
    TaxRecognitionJournalError
):
    """A journal entry already exists for this immutable event."""


class TaxRecognitionJournalNotFoundError(
    TaxRecognitionJournalError
):
    """Required tax-recognition journal entry does not exist."""


class TaxRecognitionJournalCurrencyError(
    TaxRecognitionJournalError
):
    """Tax-recognition accounting currency is unsupported."""


def validate_output_vat_recognition_accounting_currency(
    event: TaxRecognitionEvent,
) -> None:
    if event.currency_code != "UAH":
        raise TaxRecognitionJournalCurrencyError(
            "OUTPUT VAT recognition accounting currently "
            "supports UAH only"
        )


def resolve_output_vat_recognition_source_kind(
    event: TaxRecognitionEvent,
) -> OutputVatRecognitionSourceKind:
    fulfillment_selected = (
        event.invoice_fulfillment_allocation_id
        is not None
    )
    settlement_selected = (
        event.payment_settlement_allocation_id
        is not None
    )

    if (
        fulfillment_selected
        == settlement_selected
    ):
        raise TaxRecognitionJournalSourceStateError(
            "Automatic OUTPUT VAT recognition event "
            "must have exactly one typed source"
        )

    if fulfillment_selected:
        if (
            event.invoice_fulfillment_allocation_id
            <= 0
        ):
            raise TaxRecognitionJournalSourceStateError(
                "Invoice fulfillment allocation source "
                "must be greater than zero"
            )

        return (
            OutputVatRecognitionSourceKind
            .FULFILLMENT
        )

    if (
        event.payment_settlement_allocation_id
        <= 0
    ):
        raise TaxRecognitionJournalSourceStateError(
            "Payment settlement allocation source "
            "must be greater than zero"
        )

    return (
        OutputVatRecognitionSourceKind
        .SETTLEMENT
    )


def _validate_event_identity(
    event: TaxRecognitionEvent,
) -> None:
    if event.id is None or event.id <= 0:
        raise TaxRecognitionJournalSourceStateError(
            "Tax Recognition event must have "
            "a persistent positive ID"
        )

    if event.company_id <= 0:
        raise TaxRecognitionJournalSourceStateError(
            "Tax Recognition event company_id "
            "must be greater than zero"
        )


async def _build_journal_lines(
    db: AsyncSession,
    *,
    company_id: int,
    plan: TaxRecognitionAccountingPlan,
    description: str,
) -> list[JournalEntryLine]:
    try:
        accounts = await resolve_company_account_roles(
            db,
            company_id=company_id,
            roles=(
                required_roles_for_output_vat_plan(
                    plan
                )
            ),
        )
    except AccountingAccountRoleResolutionError as exc:
        raise TaxRecognitionJournalError(
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
        raise TaxRecognitionJournalError(
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
        raise TaxRecognitionJournalError(
            str(exc)
        ) from exc


async def generate_and_post_output_vat_recognition_journal_entry(
    db: AsyncSession,
    *,
    event: TaxRecognitionEvent,
    created_by: int,
) -> JournalEntry | None:
    """
    Post one original immutable OUTPUT VAT recognition event.

    Fulfillment source:
        Dr GOODS_REVENUE
        Cr TAX_SETTLEMENT

    Settlement source:
        Dr VAT_OUTPUT
        Cr TAX_SETTLEMENT

    Zero tax amount intentionally produces no JournalEntry.
    A 0% VAT recognition may still recognize taxable base while
    having no GL tax-liability amount to post.
    """
    if created_by <= 0:
        raise TaxRecognitionJournalSourceStateError(
            "created_by must be greater than zero"
        )

    _validate_event_identity(
        event
    )

    if event.reversal_of_id is not None:
        raise TaxRecognitionJournalSourceStateError(
            "A Tax Recognition reversal event "
            "cannot generate an original journal entry"
        )

    validate_output_vat_recognition_accounting_currency(
        event
    )

    source_kind = (
        resolve_output_vat_recognition_source_kind(
            event
        )
    )

    amount = Decimal(
        str(
            event.recognized_tax_amount
        )
    )

    if amount < ZERO:
        raise TaxRecognitionJournalSourceStateError(
            "Recognized OUTPUT VAT amount "
            "cannot be negative"
        )

    if amount == ZERO:
        return None

    existing_id = (
        await db.execute(
            select(
                JournalEntry.id
            ).where(
                JournalEntry.company_id
                == event.company_id,
                JournalEntry.tax_recognition_event_id
                == event.id,
                JournalEntry.reversal_of_id.is_(
                    None
                ),
            )
        )
    ).scalar_one_or_none()

    if existing_id is not None:
        raise TaxRecognitionJournalDuplicateError(
            "Original journal entry already exists "
            "for this Tax Recognition event"
        )

    try:
        plan = (
            create_output_vat_recognition_accounting_plan(
                source_kind=source_kind,
                amount=amount,
            )
        )
    except TaxRecognitionAccountingError as exc:
        raise TaxRecognitionJournalError(
            str(exc)
        ) from exc

    description = (
        "OUTPUT VAT Recognition event "
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
        tax_recognition_event_id=event.id,
        sales_recognition_event_id=None,
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


async def get_original_output_vat_recognition_journal_entry(
    db: AsyncSession,
    *,
    company_id: int,
    tax_recognition_event_id: int,
    lock: bool = False,
) -> JournalEntry:
    if company_id <= 0:
        raise ValueError(
            "company_id must be greater than zero"
        )

    if tax_recognition_event_id <= 0:
        raise ValueError(
            "tax_recognition_event_id must be "
            "greater than zero"
        )

    statement = (
        select(
            JournalEntry
        )
        .where(
            JournalEntry.company_id
            == company_id,
            JournalEntry.tax_recognition_event_id
            == tax_recognition_event_id,
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
        raise TaxRecognitionJournalNotFoundError(
            "Original journal entry not found "
            "for Tax Recognition event"
        )

    return entry

async def reverse_output_vat_recognition_journal_entry(
    db: AsyncSession,
    *,
    reversal_event: TaxRecognitionEvent,
    reversed_by: int,
) -> JournalEntry | None:
    """
    Account for an immutable OUTPUT VAT recognition reversal.

    Positive-tax event:
        reverse the original JournalEntry and bind the new
        reversal JournalEntry to this reversal TaxRecognitionEvent.

    Zero-tax event:
        no GL entry existed for the original event, therefore
        reversal is also a GL no-op.
    """
    if reversed_by <= 0:
        raise TaxRecognitionJournalSourceStateError(
            "reversed_by must be greater than zero"
        )

    _validate_event_identity(
        reversal_event
    )

    if reversal_event.reversal_of_id is None:
        raise TaxRecognitionJournalSourceStateError(
            "Only a Tax Recognition reversal event "
            "can reverse OUTPUT VAT accounting"
        )

    if reversal_event.reversal_of_id <= 0:
        raise TaxRecognitionJournalSourceStateError(
            "Tax Recognition reversal_of_id "
            "must be greater than zero"
        )

    validate_output_vat_recognition_accounting_currency(
        reversal_event
    )

    resolve_output_vat_recognition_source_kind(
        reversal_event
    )

    amount = Decimal(
        str(
            reversal_event.recognized_tax_amount
        )
    )

    if amount < ZERO:
        raise TaxRecognitionJournalSourceStateError(
            "Recognized OUTPUT VAT amount "
            "cannot be negative"
        )

    if amount == ZERO:
        return None

    existing_reversal_id = (
        await db.execute(
            select(
                JournalEntry.id
            ).where(
                JournalEntry.company_id
                == reversal_event.company_id,
                JournalEntry.tax_recognition_event_id
                == reversal_event.id,
            )
        )
    ).scalar_one_or_none()

    if existing_reversal_id is not None:
        raise TaxRecognitionJournalDuplicateError(
            "Journal entry already exists for this "
            "Tax Recognition reversal event"
        )

    original = (
        await get_original_output_vat_recognition_journal_entry(
            db,
            company_id=reversal_event.company_id,
            tax_recognition_event_id=(
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
            tax_recognition_event_id_override=(
                reversal_event.id
            ),
        )
    except AccountingReversalError as exc:
        raise TaxRecognitionJournalError(
            str(exc)
        ) from exc
