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
from app.models.purchase_return_input_vat_credit_correction_event import (
    PurchaseReturnInputVatCreditCorrectionEvent,
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
from app.services.purchase_return_input_vat_credit_correction_accounting_service import (
    PurchaseReturnInputVatCreditCorrectionAccountingError,
    create_purchase_return_input_vat_credit_correction_accounting_plan,
    required_roles_for_purchase_return_input_vat_credit_correction_plan,
)


ZERO = Decimal("0")


class PurchaseReturnInputVatCreditCorrectionJournalError(
    Exception
):
    """Base Purchase Return INPUT VAT credit correction journal error."""


class PurchaseReturnInputVatCreditCorrectionJournalSourceStateError(
    PurchaseReturnInputVatCreditCorrectionJournalError
):
    """INPUT VAT credit correction event is invalid for requested GL action."""


class PurchaseReturnInputVatCreditCorrectionJournalDuplicateError(
    PurchaseReturnInputVatCreditCorrectionJournalError
):
    """Original journal already exists for immutable VAT event."""


class PurchaseReturnInputVatCreditCorrectionJournalNotFoundError(
    PurchaseReturnInputVatCreditCorrectionJournalError
):
    """Required original INPUT VAT credit correction journal does not exist."""


class PurchaseReturnInputVatCreditCorrectionJournalCurrencyError(
    PurchaseReturnInputVatCreditCorrectionJournalError
):
    """Purchase Return INPUT VAT credit correction accounting currency unsupported."""


def validate_purchase_return_input_vat_credit_correction_accounting_currency(
    event: PurchaseReturnInputVatCreditCorrectionEvent,
) -> None:
    if event.currency_code != "UAH":
        raise (
            PurchaseReturnInputVatCreditCorrectionJournalCurrencyError(
                "Purchase Return INPUT VAT credit correction accounting "
                "currently supports UAH only"
            )
        )


def _validate_event_identity(
    event: PurchaseReturnInputVatCreditCorrectionEvent,
) -> None:
    if (
        event.id is None
        or event.id <= 0
    ):
        raise (
            PurchaseReturnInputVatCreditCorrectionJournalSourceStateError(
                "Purchase Return INPUT VAT credit correction event "
                "must have a persistent positive ID"
            )
        )

    if event.company_id <= 0:
        raise (
            PurchaseReturnInputVatCreditCorrectionJournalSourceStateError(
                "Purchase Return INPUT VAT credit correction event "
                "company_id must be greater than zero"
            )
        )


def _event_amount(
    event: PurchaseReturnInputVatCreditCorrectionEvent,
) -> Decimal:
    try:
        amount = Decimal(
            str(
                event.reduced_tax_amount
            )
        )
    except Exception as exc:
        raise (
            PurchaseReturnInputVatCreditCorrectionJournalSourceStateError(
                "Purchase Return INPUT VAT credit correction amount "
                "must be Decimal-compatible"
            )
        ) from exc

    if not amount.is_finite():
        raise (
            PurchaseReturnInputVatCreditCorrectionJournalSourceStateError(
                "Purchase Return INPUT VAT credit correction amount "
                "must be finite"
            )
        )

    if amount < ZERO:
        raise (
            PurchaseReturnInputVatCreditCorrectionJournalSourceStateError(
                "Purchase Return INPUT VAT credit correction amount "
                "cannot be negative"
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
                    required_roles_for_purchase_return_input_vat_credit_correction_plan(
                        plan
                    )
                ),
            )
        )
    except (
        AccountingAccountRoleResolutionError
    ) as exc:
        raise (
            PurchaseReturnInputVatCreditCorrectionJournalError(
                str(
                    exc
                )
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
        raise (
            PurchaseReturnInputVatCreditCorrectionJournalError(
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
            PurchaseReturnInputVatCreditCorrectionJournalError(
                str(
                    exc
                )
            )
        ) from exc


async def generate_and_post_purchase_return_input_vat_credit_correction_journal_entry(
    db: AsyncSession,
    *,
    event: PurchaseReturnInputVatCreditCorrectionEvent,
    created_by: int,
) -> JournalEntry | None:
    """Post the buyer-side legal INPUT VAT credit decrease.

    Original:
        Dr VAT_INPUT / 644
        Cr TAX_SETTLEMENT / 641

    amount = reduced_tax_amount only.

    A positive taxable-base / zero-tax immutable legal correction
    event produces no zero JournalEntry.
    """

    if created_by <= 0:
        raise (
            PurchaseReturnInputVatCreditCorrectionJournalSourceStateError(
                "created_by must be greater than zero"
            )
        )

    _validate_event_identity(
        event
    )

    if event.reversal_of_id is not None:
        raise (
            PurchaseReturnInputVatCreditCorrectionJournalSourceStateError(
                "INPUT VAT credit correction reversal event cannot "
                "generate an original journal entry"
            )
        )

    validate_purchase_return_input_vat_credit_correction_accounting_currency(
        event
    )

    amount = _event_amount(
        event
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
                (
                    JournalEntry
                    .purchase_return_input_vat_credit_correction_event_id
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
            PurchaseReturnInputVatCreditCorrectionJournalDuplicateError(
                "Original JournalEntry already exists "
                "for Purchase Return INPUT VAT credit correction event"
            )
        )

    try:
        plan = (
            create_purchase_return_input_vat_credit_correction_accounting_plan(
                amount=amount
            )
        )
    except (
        PurchaseReturnInputVatCreditCorrectionAccountingError
    ) as exc:
        raise (
            PurchaseReturnInputVatCreditCorrectionJournalError(
                str(
                    exc
                )
            )
        ) from exc

    description = (
        "Purchase Return INPUT VAT Credit Correction event "
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
        purchase_return_recognition_event_id=None,
        purchase_return_input_vat_credit_correction_event_id=event.id,
        accounting_rule_id=None,
        entry_date=event.adjustment_date,
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


async def get_original_purchase_return_input_vat_credit_correction_journal_entry(
    db: AsyncSession,
    *,
    company_id: int,
    purchase_return_input_vat_credit_correction_event_id: int,
    lock: bool = False,
) -> JournalEntry:
    if company_id <= 0:
        raise ValueError(
            "company_id must be greater than zero"
        )

    if (
        purchase_return_input_vat_credit_correction_event_id
        <= 0
    ):
        raise ValueError(
            "purchase_return_input_vat_credit_correction_event_id "
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
                .purchase_return_input_vat_credit_correction_event_id
                == purchase_return_input_vat_credit_correction_event_id
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
            PurchaseReturnInputVatCreditCorrectionJournalNotFoundError(
                "Original JournalEntry not found for "
                "Purchase Return INPUT VAT credit correction event"
            )
        )

    return entry


async def reverse_purchase_return_input_vat_credit_correction_journal_entry(
    db: AsyncSession,
    *,
    reversal_event: PurchaseReturnInputVatCreditCorrectionEvent,
    reversed_by: int,
) -> JournalEntry | None:
    """Reverse one immutable legal INPUT VAT credit correction.

    Reversal:
        Dr TAX_SETTLEMENT / 641
        Cr VAT_INPUT / 644

    The reversal JournalEntry is bound to the immutable legal
    correction reversal event.

    A zero-tax reversal produces no zero JournalEntry.
    """

    if reversed_by <= 0:
        raise (
            PurchaseReturnInputVatCreditCorrectionJournalSourceStateError(
                "reversed_by must be greater than zero"
            )
        )

    _validate_event_identity(
        reversal_event
    )

    if reversal_event.reversal_of_id is None:
        raise (
            PurchaseReturnInputVatCreditCorrectionJournalSourceStateError(
                "Only a INPUT VAT credit correction reversal event "
                "can reverse INPUT VAT credit correction accounting"
            )
        )

    if reversal_event.reversal_of_id <= 0:
        raise (
            PurchaseReturnInputVatCreditCorrectionJournalSourceStateError(
                "reversal_of_id must be greater than zero"
            )
        )

    validate_purchase_return_input_vat_credit_correction_accounting_currency(
        reversal_event
    )

    amount = _event_amount(
        reversal_event
    )

    if amount == ZERO:
        unexpected = (
            await db.execute(
                select(
                    JournalEntry.id
                ).where(
                    JournalEntry.company_id
                    == reversal_event.company_id,
                    (
                        JournalEntry
                        .purchase_return_input_vat_credit_correction_event_id
                        == reversal_event.reversal_of_id
                    ),
                    JournalEntry.reversal_of_id.is_(
                        None
                    ),
                )
            )
        ).scalar_one_or_none()

        if unexpected is not None:
            raise (
                PurchaseReturnInputVatCreditCorrectionJournalSourceStateError(
                    "Zero-tax INPUT VAT credit correction unexpectedly "
                    "has an original JournalEntry"
                )
            )

        return None

    original = (
        await get_original_purchase_return_input_vat_credit_correction_journal_entry(
            db,
            company_id=reversal_event.company_id,
            purchase_return_input_vat_credit_correction_event_id=(
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
            reversal_date=reversal_event.adjustment_date,
            reversed_by=reversed_by,
            purchase_return_input_vat_credit_correction_event_id_override=(
                reversal_event.id
            ),
        )
    except AccountingReversalError as exc:
        raise (
            PurchaseReturnInputVatCreditCorrectionJournalError(
                str(
                    exc
                )
            )
        ) from exc
