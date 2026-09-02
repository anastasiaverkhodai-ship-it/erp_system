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
from app.models.vat_advance_bridge_event import (
    VatAdvanceBridgeEvent,
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
from app.services.vat_advance_bridge_accounting_service import (
    VatAdvanceBridgeAccountingError,
    VatAdvanceBridgeAccountingPlan,
    create_vat_advance_bridge_accounting_plan,
    required_roles_for_vat_advance_bridge_plan,
)


ZERO = Decimal("0")


class VatAdvanceBridgeJournalError(Exception):
    """Base VAT Advance Bridge journal error."""


class VatAdvanceBridgeJournalSourceStateError(
    VatAdvanceBridgeJournalError
):
    """VAT Advance Bridge event is invalid for this GL action."""


class VatAdvanceBridgeJournalDuplicateError(
    VatAdvanceBridgeJournalError
):
    """A journal entry already exists for this immutable event."""


class VatAdvanceBridgeJournalNotFoundError(
    VatAdvanceBridgeJournalError
):
    """Required VAT Advance Bridge journal entry does not exist."""


class VatAdvanceBridgeJournalCurrencyError(
    VatAdvanceBridgeJournalError
):
    """VAT Advance Bridge accounting currency is unsupported."""


def validate_vat_advance_bridge_accounting_currency(
    event: VatAdvanceBridgeEvent,
) -> None:
    if event.currency_code != "UAH":
        raise VatAdvanceBridgeJournalCurrencyError(
            "VAT Advance Bridge accounting currently "
            "supports UAH only"
        )


def _validate_event_identity(
    event: VatAdvanceBridgeEvent,
) -> None:
    if event.id is None or event.id <= 0:
        raise VatAdvanceBridgeJournalSourceStateError(
            "VAT Advance Bridge event must have "
            "a persistent positive ID"
        )

    if event.company_id <= 0:
        raise VatAdvanceBridgeJournalSourceStateError(
            "VAT Advance Bridge event company_id "
            "must be greater than zero"
        )


def _event_amount(
    event: VatAdvanceBridgeEvent,
) -> Decimal:
    amount = Decimal(
        str(
            event.bridged_tax_amount
        )
    )

    if not amount.is_finite():
        raise VatAdvanceBridgeJournalSourceStateError(
            "VAT Advance Bridge amount must be finite"
        )

    if amount <= ZERO:
        raise VatAdvanceBridgeJournalSourceStateError(
            "VAT Advance Bridge amount "
            "must be greater than zero"
        )

    return amount


async def _build_journal_lines(
    db: AsyncSession,
    *,
    company_id: int,
    plan: VatAdvanceBridgeAccountingPlan,
    description: str,
) -> list[JournalEntryLine]:
    try:
        accounts = await resolve_company_account_roles(
            db,
            company_id=company_id,
            roles=(
                required_roles_for_vat_advance_bridge_plan(
                    plan
                )
            ),
        )
    except AccountingAccountRoleResolutionError as exc:
        raise VatAdvanceBridgeJournalError(
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
        raise VatAdvanceBridgeJournalError(
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
        raise VatAdvanceBridgeJournalError(
            str(exc)
        ) from exc


async def generate_and_post_vat_advance_bridge_journal_entry(
    db: AsyncSession,
    *,
    event: VatAdvanceBridgeEvent,
    created_by: int,
) -> JournalEntry:
    """
    Post one original immutable VatAdvanceBridgeEvent:

        Dr GOODS_REVENUE
        Cr VAT_OUTPUT

    Amount is bridged_tax_amount.
    """

    if created_by <= 0:
        raise VatAdvanceBridgeJournalSourceStateError(
            "created_by must be greater than zero"
        )

    _validate_event_identity(
        event
    )

    if event.reversal_of_id is not None:
        raise VatAdvanceBridgeJournalSourceStateError(
            "A VAT Advance Bridge reversal event "
            "cannot generate an original journal entry"
        )

    validate_vat_advance_bridge_accounting_currency(
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
                JournalEntry.vat_advance_bridge_event_id
                == event.id,
                JournalEntry.reversal_of_id.is_(
                    None
                ),
            )
        )
    ).scalar_one_or_none()

    if existing_id is not None:
        raise VatAdvanceBridgeJournalDuplicateError(
            "Original journal entry already exists "
            "for this VAT Advance Bridge event"
        )

    try:
        plan = (
            create_vat_advance_bridge_accounting_plan(
                amount=amount,
            )
        )
    except VatAdvanceBridgeAccountingError as exc:
        raise VatAdvanceBridgeJournalError(
            str(exc)
        ) from exc

    description = (
        "VAT Advance Bridge event "
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
        vat_advance_bridge_event_id=event.id,
        accounting_rule_id=None,
        entry_date=event.bridge_date,
        description=description,
        status=JournalEntryStatus.DRAFT,
        created_by=created_by,
    )

    journal_entry.lines = lines

    return await _validate_and_post(
        db,
        journal_entry=journal_entry,
    )


async def get_original_vat_advance_bridge_journal_entry(
    db: AsyncSession,
    *,
    company_id: int,
    vat_advance_bridge_event_id: int,
    lock: bool = False,
) -> JournalEntry:
    if company_id <= 0:
        raise ValueError(
            "company_id must be greater than zero"
        )

    if vat_advance_bridge_event_id <= 0:
        raise ValueError(
            "vat_advance_bridge_event_id must be "
            "greater than zero"
        )

    statement = (
        select(
            JournalEntry
        )
        .where(
            JournalEntry.company_id
            == company_id,
            JournalEntry.vat_advance_bridge_event_id
            == vat_advance_bridge_event_id,
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
        raise VatAdvanceBridgeJournalNotFoundError(
            "Original journal entry not found "
            "for VAT Advance Bridge event"
        )

    return entry


async def reverse_vat_advance_bridge_journal_entry(
    db: AsyncSession,
    *,
    reversal_event: VatAdvanceBridgeEvent,
    reversed_by: int,
) -> JournalEntry:
    """
    Account for an immutable VatAdvanceBridgeEvent reversal.

    Original bridge:
        Dr GOODS_REVENUE
        Cr VAT_OUTPUT

    Generic JournalEntry reversal:
        Dr VAT_OUTPUT
        Cr GOODS_REVENUE

    Reversal JournalEntry is bound to the immutable
    reversal VatAdvanceBridgeEvent.
    """

    if reversed_by <= 0:
        raise VatAdvanceBridgeJournalSourceStateError(
            "reversed_by must be greater than zero"
        )

    _validate_event_identity(
        reversal_event
    )

    if reversal_event.reversal_of_id is None:
        raise VatAdvanceBridgeJournalSourceStateError(
            "Only a VAT Advance Bridge reversal event "
            "can reverse VAT Advance Bridge accounting"
        )

    if reversal_event.reversal_of_id <= 0:
        raise VatAdvanceBridgeJournalSourceStateError(
            "VAT Advance Bridge reversal_of_id "
            "must be greater than zero"
        )

    validate_vat_advance_bridge_accounting_currency(
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
                JournalEntry.vat_advance_bridge_event_id
                == reversal_event.id,
            )
        )
    ).scalar_one_or_none()

    if existing_reversal_id is not None:
        raise VatAdvanceBridgeJournalDuplicateError(
            "Journal entry already exists for this "
            "VAT Advance Bridge reversal event"
        )

    original = (
        await get_original_vat_advance_bridge_journal_entry(
            db,
            company_id=reversal_event.company_id,
            vat_advance_bridge_event_id=(
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
                reversal_event.bridge_date
            ),
            reversed_by=reversed_by,
            vat_advance_bridge_event_id_override=(
                reversal_event.id
            ),
        )
    except AccountingReversalError as exc:
        raise VatAdvanceBridgeJournalError(
            str(exc)
        ) from exc
