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
from app.models.purchase_return_recognition_event import (
    PurchaseReturnRecognitionEvent,
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
from app.services.purchase_return_recognition_accounting_service import (
    PurchaseReturnRecognitionAccountingError,
    PurchaseReturnRecognitionAccountingPlan,
    create_purchase_return_recognition_accounting_plan,
    required_roles_for_purchase_return_recognition_plan,
)


ZERO = Decimal("0")


class PurchaseReturnRecognitionJournalError(
    Exception
):
    """Base Purchase Return Recognition journal error."""


class PurchaseReturnRecognitionJournalSourceStateError(
    PurchaseReturnRecognitionJournalError
):
    """Purchase Return Recognition event is invalid for GL."""


class PurchaseReturnRecognitionJournalDuplicateError(
    PurchaseReturnRecognitionJournalError
):
    """A JournalEntry already exists for this immutable event."""


class PurchaseReturnRecognitionJournalNotFoundError(
    PurchaseReturnRecognitionJournalError
):
    """Required Purchase Return Recognition JournalEntry is absent."""


class PurchaseReturnRecognitionJournalCurrencyError(
    PurchaseReturnRecognitionJournalError
):
    """Purchase Return Recognition accounting currency is unsupported."""


def validate_purchase_return_recognition_accounting_currency(
    event: PurchaseReturnRecognitionEvent,
) -> None:
    if event.currency_code != "UAH":
        raise PurchaseReturnRecognitionJournalCurrencyError(
            "Purchase Return Recognition accounting "
            "currently supports UAH only"
        )


def _validate_event_identity(
    event: PurchaseReturnRecognitionEvent,
) -> None:
    if (
        event.id is None
        or event.id <= 0
    ):
        raise PurchaseReturnRecognitionJournalSourceStateError(
            "Purchase Return Recognition event must "
            "have a persistent positive ID"
        )

    if event.company_id <= 0:
        raise PurchaseReturnRecognitionJournalSourceStateError(
            "Purchase Return Recognition event company_id "
            "must be greater than zero"
        )


def _event_amount(
    event: PurchaseReturnRecognitionEvent,
) -> Decimal:
    try:
        amount = Decimal(
            str(
                event.returned_base_amount
            )
        )
    except Exception as exc:
        raise PurchaseReturnRecognitionJournalSourceStateError(
            "returned_base_amount must be Decimal-compatible"
        ) from exc

    if not amount.is_finite():
        raise PurchaseReturnRecognitionJournalSourceStateError(
            "returned_base_amount must be finite"
        )

    if amount < ZERO:
        raise PurchaseReturnRecognitionJournalSourceStateError(
            "returned_base_amount cannot be negative"
        )

    return amount


async def _build_journal_lines(
    db: AsyncSession,
    *,
    company_id: int,
    plan: PurchaseReturnRecognitionAccountingPlan,
    description: str,
) -> list[
    JournalEntryLine
]:
    try:
        accounts = await resolve_company_account_roles(
            db,
            company_id=company_id,
            roles=(
                required_roles_for_purchase_return_recognition_plan(
                    plan
                )
            ),
        )
    except AccountingAccountRoleResolutionError as exc:
        raise PurchaseReturnRecognitionJournalError(
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
        raise PurchaseReturnRecognitionJournalError(
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
        raise PurchaseReturnRecognitionJournalError(
            str(
                exc
            )
        ) from exc


async def generate_and_post_purchase_return_recognition_journal_entry(
    db: AsyncSession,
    *,
    event: PurchaseReturnRecognitionEvent,
    created_by: int,
) -> JournalEntry | None:
    """
    Account for one original immutable PurchaseReturnRecognitionEvent.

    Positive returned_base_amount:

        Dr SUPPLIER_PAYABLES
        Cr INVENTORY_GOODS

    GENERAL 291:

        Dr 631
        Cr 281

    Only returned_base_amount is posted.

    returned_gross_amount and returned_tax_amount are commercial / tax
    snapshots and are intentionally excluded from this GL milestone.

    A legitimate zero-base Purchase Return event creates no JournalEntry.

    Caller owns COMMIT / ROLLBACK.
    """
    if created_by <= 0:
        raise PurchaseReturnRecognitionJournalSourceStateError(
            "created_by must be greater than zero"
        )

    _validate_event_identity(
        event
    )

    if event.reversal_of_id is not None:
        raise PurchaseReturnRecognitionJournalSourceStateError(
            "A Purchase Return Recognition reversal event "
            "cannot generate an original journal entry"
        )

    validate_purchase_return_recognition_accounting_currency(
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
                    .purchase_return_recognition_event_id
                    == event.id
                ),
                JournalEntry.reversal_of_id.is_(
                    None
                ),
            )
        )
    ).scalar_one_or_none()

    if existing_id is not None:
        raise PurchaseReturnRecognitionJournalDuplicateError(
            "Original journal entry already exists "
            "for this Purchase Return Recognition event"
        )

    if amount == ZERO:
        return None

    try:
        plan = (
            create_purchase_return_recognition_accounting_plan(
                amount=amount,
            )
        )
    except PurchaseReturnRecognitionAccountingError as exc:
        raise PurchaseReturnRecognitionJournalError(
            str(
                exc
            )
        ) from exc

    description = (
        "Purchase Return Recognition event "
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
        sales_return_cost_restoration_event_id=None,
        purchase_return_recognition_event_id=(
            event.id
        ),
        accounting_rule_id=None,
        entry_date=event.recognition_date,
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


async def get_original_purchase_return_recognition_journal_entry(
    db: AsyncSession,
    *,
    company_id: int,
    purchase_return_recognition_event_id: int,
    lock: bool = False,
) -> JournalEntry:
    if company_id <= 0:
        raise ValueError(
            "company_id must be greater than zero"
        )

    if purchase_return_recognition_event_id <= 0:
        raise ValueError(
            "purchase_return_recognition_event_id "
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
                .purchase_return_recognition_event_id
                == purchase_return_recognition_event_id
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
        raise PurchaseReturnRecognitionJournalNotFoundError(
            "Original journal entry not found "
            "for Purchase Return Recognition event"
        )

    return entry


async def reverse_purchase_return_recognition_journal_entry(
    db: AsyncSession,
    *,
    reversal_event: PurchaseReturnRecognitionEvent,
    reversed_by: int,
) -> JournalEntry | None:
    """
    Account for an immutable Purchase Return Recognition reversal.

    Original:

        Dr SUPPLIER_PAYABLES
        Cr INVENTORY_GOODS

        Dr 631 / Cr 281

    Generic JournalEntry reversal:

        Dr INVENTORY_GOODS
        Cr SUPPLIER_PAYABLES

        Dr 281 / Cr 631

    The reversal JournalEntry is typed by the immutable reversal
    PurchaseReturnRecognitionEvent through the dedicated source override.

    A zero-base event has no original GL entry and therefore needs no
    GL reversal.

    Caller owns COMMIT / ROLLBACK.
    """
    if reversed_by <= 0:
        raise PurchaseReturnRecognitionJournalSourceStateError(
            "reversed_by must be greater than zero"
        )

    _validate_event_identity(
        reversal_event
    )

    if reversal_event.reversal_of_id is None:
        raise PurchaseReturnRecognitionJournalSourceStateError(
            "Only a Purchase Return Recognition reversal event "
            "can reverse Purchase Return Recognition accounting"
        )

    if reversal_event.reversal_of_id <= 0:
        raise PurchaseReturnRecognitionJournalSourceStateError(
            "Purchase Return Recognition reversal_of_id "
            "must be greater than zero"
        )

    validate_purchase_return_recognition_accounting_currency(
        reversal_event
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
                    .purchase_return_recognition_event_id
                    == reversal_event.id
                ),
            )
        )
    ).scalar_one_or_none()

    if existing_reversal_id is not None:
        raise PurchaseReturnRecognitionJournalDuplicateError(
            "Journal entry already exists for this "
            "Purchase Return Recognition reversal event"
        )

    if amount == ZERO:
        return None

    original = (
        await get_original_purchase_return_recognition_journal_entry(
            db,
            company_id=reversal_event.company_id,
            purchase_return_recognition_event_id=(
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
            purchase_return_recognition_event_id_override=(
                reversal_event.id
            ),
        )
    except AccountingReversalError as exc:
        raise PurchaseReturnRecognitionJournalError(
            str(
                exc
            )
        ) from exc
