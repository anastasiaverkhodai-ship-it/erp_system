from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.input_vat_fulfillment_bridge_event import (
    InputVatFulfillmentBridgeEvent,
)
from app.models.journal_entry import (
    JournalEntry,
    JournalEntryStatus,
)
from app.models.journal_entry_line import (
    JournalEntryLine,
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
from app.services.input_vat_accounting_service import (
    create_input_vat_fulfillment_bridge_accounting_plan,
    required_roles_for_input_vat_plan,
)


ZERO = Decimal("0")


class InputVatFulfillmentBridgeJournalError(
    Exception
):
    """Base economic INPUT VAT bridge journal error."""


class InputVatFulfillmentBridgeJournalSourceStateError(
    InputVatFulfillmentBridgeJournalError
):
    """Bridge event is invalid for the requested GL action."""


class InputVatFulfillmentBridgeJournalDuplicateError(
    InputVatFulfillmentBridgeJournalError
):
    """A journal already exists for this immutable bridge event."""


class InputVatFulfillmentBridgeJournalNotFoundError(
    InputVatFulfillmentBridgeJournalError
):
    """Required INPUT VAT bridge journal does not exist."""


class InputVatFulfillmentBridgeJournalCurrencyError(
    InputVatFulfillmentBridgeJournalError
):
    """Economic INPUT VAT bridge accounting currency is unsupported."""


def validate_input_vat_fulfillment_bridge_accounting_currency(
    event: InputVatFulfillmentBridgeEvent,
) -> None:
    if event.currency_code != "UAH":
        raise (
            InputVatFulfillmentBridgeJournalCurrencyError(
                "INPUT VAT fulfillment bridge accounting "
                "currently supports UAH only"
            )
        )


def _validate_event_identity(
    event: InputVatFulfillmentBridgeEvent,
) -> None:
    if (
        event.id is None
        or event.id <= 0
    ):
        raise (
            InputVatFulfillmentBridgeJournalSourceStateError(
                "INPUT VAT fulfillment bridge event "
                "must have a persistent positive ID"
            )
        )

    if event.company_id <= 0:
        raise (
            InputVatFulfillmentBridgeJournalSourceStateError(
                "INPUT VAT fulfillment bridge event "
                "company_id must be greater than zero"
            )
        )


def _event_amount(
    event: InputVatFulfillmentBridgeEvent,
) -> Decimal:
    amount = Decimal(
        str(
            event.bridged_tax_amount
        )
    )

    if not amount.is_finite():
        raise (
            InputVatFulfillmentBridgeJournalSourceStateError(
                "INPUT VAT fulfillment bridge amount "
                "must be finite"
            )
        )

    if amount <= ZERO:
        raise (
            InputVatFulfillmentBridgeJournalSourceStateError(
                "INPUT VAT fulfillment bridge amount "
                "must be greater than zero"
            )
        )

    return amount


async def _build_journal_lines(
    db: AsyncSession,
    *,
    company_id: int,
    plan,
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
                    required_roles_for_input_vat_plan(
                        plan
                    )
                ),
            )
        )
    except (
        AccountingAccountRoleResolutionError
    ) as exc:
        raise (
            InputVatFulfillmentBridgeJournalError(
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
            InputVatFulfillmentBridgeJournalError(
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
            company_id=(
                journal_entry.company_id
            ),
            journal_entry_id=(
                journal_entry.id
            ),
        )
    except AccountingPostingError as exc:
        raise (
            InputVatFulfillmentBridgeJournalError(
                str(
                    exc
                )
            )
        ) from exc


async def generate_and_post_input_vat_fulfillment_bridge_journal_entry(
    db: AsyncSession,
    *,
    event: InputVatFulfillmentBridgeEvent,
    created_by: int,
) -> JournalEntry:
    """
    Post one original immutable economic INPUT VAT bridge event:

        Dr VAT_INPUT
        Cr SUPPLIER_PAYABLES

    GENERAL 291:

        Dr 644
        Cr 631

    Amount is bridged_tax_amount.

    Tax-credit recognition is separate and later posts:

        Dr TAX_SETTLEMENT
        Cr VAT_INPUT
    """

    if created_by <= 0:
        raise (
            InputVatFulfillmentBridgeJournalSourceStateError(
                "created_by must be greater than zero"
            )
        )

    _validate_event_identity(
        event
    )

    if event.reversal_of_id is not None:
        raise (
            InputVatFulfillmentBridgeJournalSourceStateError(
                "An INPUT VAT fulfillment bridge "
                "reversal event cannot generate "
                "an original journal entry"
            )
        )

    validate_input_vat_fulfillment_bridge_accounting_currency(
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
                    .input_vat_fulfillment_bridge_event_id
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
            InputVatFulfillmentBridgeJournalDuplicateError(
                "Original journal entry already exists "
                "for this INPUT VAT fulfillment "
                "bridge event"
            )
        )

    plan = (
        create_input_vat_fulfillment_bridge_accounting_plan(
            amount=amount
        )
    )

    description = (
        "INPUT VAT Fulfillment Bridge event "
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
        input_vat_fulfillment_bridge_event_id=(
            event.id
        ),
        accounting_rule_id=None,
        entry_date=event.bridge_date,
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


async def get_original_input_vat_fulfillment_bridge_journal_entry(
    db: AsyncSession,
    *,
    company_id: int,
    input_vat_fulfillment_bridge_event_id: int,
    lock: bool = False,
) -> JournalEntry:
    if company_id <= 0:
        raise ValueError(
            "company_id must be greater than zero"
        )

    if (
        input_vat_fulfillment_bridge_event_id
        <= 0
    ):
        raise ValueError(
            "input_vat_fulfillment_bridge_event_id "
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
                .input_vat_fulfillment_bridge_event_id
                == (
                    input_vat_fulfillment_bridge_event_id
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
        raise (
            InputVatFulfillmentBridgeJournalNotFoundError(
                "Original journal entry not found "
                "for INPUT VAT fulfillment bridge event"
            )
        )

    return entry


async def reverse_input_vat_fulfillment_bridge_journal_entry(
    db: AsyncSession,
    *,
    reversal_event: InputVatFulfillmentBridgeEvent,
    reversed_by: int,
) -> JournalEntry:
    """
    Account for an immutable INPUT VAT bridge reversal.

    Original:

        Dr VAT_INPUT
        Cr SUPPLIER_PAYABLES

    Generic JournalEntry reversal:

        Dr SUPPLIER_PAYABLES
        Cr VAT_INPUT

    The reversal JournalEntry is bound to the immutable
    reversal InputVatFulfillmentBridgeEvent.
    """

    if reversed_by <= 0:
        raise (
            InputVatFulfillmentBridgeJournalSourceStateError(
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
            InputVatFulfillmentBridgeJournalSourceStateError(
                "Only an INPUT VAT fulfillment bridge "
                "reversal event can reverse bridge accounting"
            )
        )

    if (
        reversal_event.reversal_of_id
        <= 0
    ):
        raise (
            InputVatFulfillmentBridgeJournalSourceStateError(
                "INPUT VAT fulfillment bridge "
                "reversal_of_id must be greater than zero"
            )
        )

    validate_input_vat_fulfillment_bridge_accounting_currency(
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
                    .input_vat_fulfillment_bridge_event_id
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
            InputVatFulfillmentBridgeJournalDuplicateError(
                "Journal entry already exists for this "
                "INPUT VAT fulfillment bridge "
                "reversal event"
            )
        )

    original = (
        await get_original_input_vat_fulfillment_bridge_journal_entry(
            db,
            company_id=(
                reversal_event.company_id
            ),
            input_vat_fulfillment_bridge_event_id=(
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
                reversal_event.bridge_date
            ),
            reversed_by=reversed_by,
            input_vat_fulfillment_bridge_event_id_override=(
                reversal_event.id
            ),
        )
    except AccountingReversalError as exc:
        raise (
            InputVatFulfillmentBridgeJournalError(
                str(
                    exc
                )
            )
        ) from exc
