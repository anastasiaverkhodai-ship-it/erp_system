from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.journal_entry import (
    JournalEntry,
    JournalEntryStatus,
)
from app.models.journal_entry_line import JournalEntryLine
from app.models.purchase_value_correction_vat_adjustment_event import (
    PurchaseValueCorrectionVatAdjustmentEvent,
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
from app.services.purchase_value_correction_vat_adjustment_accounting_service import (
    PurchaseValueCorrectionVatAdjustmentAccountingError,
    create_purchase_value_correction_vat_adjustment_accounting_plan,
    required_roles_for_purchase_value_correction_vat_adjustment_plan,
)


ZERO = Decimal("0")


class PurchaseValueCorrectionVatAdjustmentJournalError(Exception):
    pass


class PurchaseValueCorrectionVatAdjustmentJournalSourceStateError(
    PurchaseValueCorrectionVatAdjustmentJournalError
):
    pass


class PurchaseValueCorrectionVatAdjustmentJournalDuplicateError(
    PurchaseValueCorrectionVatAdjustmentJournalError
):
    pass


class PurchaseValueCorrectionVatAdjustmentJournalNotFoundError(
    PurchaseValueCorrectionVatAdjustmentJournalError
):
    pass


class PurchaseValueCorrectionVatAdjustmentJournalCurrencyError(
    PurchaseValueCorrectionVatAdjustmentJournalError
):
    pass


def _validate_event(
    event: PurchaseValueCorrectionVatAdjustmentEvent,
) -> Decimal:
    if event.id is None or event.id <= 0:
        raise PurchaseValueCorrectionVatAdjustmentJournalSourceStateError(
            "PVC VAT adjustment event must have a persistent positive ID"
        )

    if event.company_id <= 0:
        raise PurchaseValueCorrectionVatAdjustmentJournalSourceStateError(
            "PVC VAT adjustment company_id must be greater than zero"
        )

    if event.currency_code != "UAH":
        raise PurchaseValueCorrectionVatAdjustmentJournalCurrencyError(
            "PVC VAT adjustment accounting currently supports UAH only"
        )

    if event.adjustment_kind not in {"decrease", "increase"}:
        raise PurchaseValueCorrectionVatAdjustmentJournalSourceStateError(
            "PVC VAT adjustment_kind is invalid"
        )

    try:
        amount = Decimal(str(event.adjusted_tax_amount))
    except Exception as exc:
        raise PurchaseValueCorrectionVatAdjustmentJournalSourceStateError(
            "PVC VAT adjustment amount must be Decimal-compatible"
        ) from exc

    if not amount.is_finite() or amount < ZERO:
        raise PurchaseValueCorrectionVatAdjustmentJournalSourceStateError(
            "PVC VAT adjustment amount must be finite and non-negative"
        )

    return amount


async def _build_lines(
    db: AsyncSession,
    *,
    company_id: int,
    plan,
    description: str,
) -> list[JournalEntryLine]:
    try:
        accounts = await resolve_company_account_roles(
            db,
            company_id=company_id,
            roles=required_roles_for_purchase_value_correction_vat_adjustment_plan(
                plan
            ),
        )
    except AccountingAccountRoleResolutionError as exc:
        raise PurchaseValueCorrectionVatAdjustmentJournalError(
            str(exc)
        ) from exc

    return [
        JournalEntryLine(
            line_no=line_no,
            account_id=accounts[planned.role].id,
            debit=planned.debit,
            credit=planned.credit,
            description=description,
        )
        for line_no, planned in enumerate(plan.lines, start=1)
    ]


async def _post(
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
        raise PurchaseValueCorrectionVatAdjustmentJournalError(
            str(exc)
        ) from exc

    db.add(journal_entry)
    await db.flush()

    try:
        return await post_journal_entry(
            db=db,
            company_id=journal_entry.company_id,
            journal_entry_id=journal_entry.id,
        )
    except AccountingPostingError as exc:
        raise PurchaseValueCorrectionVatAdjustmentJournalError(
            str(exc)
        ) from exc


async def generate_and_post_purchase_value_correction_vat_adjustment_journal_entry(
    db: AsyncSession,
    *,
    event: PurchaseValueCorrectionVatAdjustmentEvent,
    created_by: int,
) -> JournalEntry | None:
    if created_by <= 0:
        raise PurchaseValueCorrectionVatAdjustmentJournalSourceStateError(
            "created_by must be greater than zero"
        )

    amount = _validate_event(event)

    if event.reversal_of_id is not None:
        raise PurchaseValueCorrectionVatAdjustmentJournalSourceStateError(
            "PVC VAT reversal event cannot generate original accounting"
        )

    if amount == ZERO:
        return None

    existing = (
        await db.execute(
            select(JournalEntry.id).where(
                JournalEntry.company_id == event.company_id,
                JournalEntry.purchase_value_correction_vat_adjustment_event_id
                == event.id,
                JournalEntry.reversal_of_id.is_(None),
            )
        )
    ).scalar_one_or_none()

    if existing is not None:
        raise PurchaseValueCorrectionVatAdjustmentJournalDuplicateError(
            "Original JournalEntry already exists for PVC VAT event"
        )

    try:
        plan = (
            create_purchase_value_correction_vat_adjustment_accounting_plan(
                amount=amount,
                adjustment_kind=event.adjustment_kind,
            )
        )
    except PurchaseValueCorrectionVatAdjustmentAccountingError as exc:
        raise PurchaseValueCorrectionVatAdjustmentJournalError(
            str(exc)
        ) from exc

    description = f"Purchase Value Correction VAT event {event.id}"

    lines = await _build_lines(
        db,
        company_id=event.company_id,
        plan=plan,
        description=description,
    )

    journal_entry = JournalEntry(
        company_id=event.company_id,
        purchase_value_correction_vat_adjustment_event_id=event.id,
        accounting_rule_id=None,
        entry_date=event.adjustment_date,
        description=description,
        status=JournalEntryStatus.DRAFT,
        created_by=created_by,
    )
    journal_entry.lines = lines

    return await _post(
        db,
        journal_entry=journal_entry,
    )


async def get_original_purchase_value_correction_vat_adjustment_journal_entry(
    db: AsyncSession,
    *,
    company_id: int,
    purchase_value_correction_vat_adjustment_event_id: int,
    lock: bool = False,
) -> JournalEntry:
    if company_id <= 0:
        raise ValueError(
            "company_id must be greater than zero"
        )

    if purchase_value_correction_vat_adjustment_event_id <= 0:
        raise ValueError(
            "purchase_value_correction_vat_adjustment_event_id "
            "must be greater than zero"
        )

    statement = select(JournalEntry).where(
        JournalEntry.company_id == company_id,
        JournalEntry.purchase_value_correction_vat_adjustment_event_id
        == purchase_value_correction_vat_adjustment_event_id,
        JournalEntry.reversal_of_id.is_(None),
    )

    if lock:
        statement = statement.with_for_update()

    entry = (
        await db.execute(statement)
    ).scalar_one_or_none()

    if entry is None:
        raise PurchaseValueCorrectionVatAdjustmentJournalNotFoundError(
            "Original PVC VAT JournalEntry not found"
        )

    return entry


async def reverse_purchase_value_correction_vat_adjustment_journal_entry(
    db: AsyncSession,
    *,
    reversal_event: PurchaseValueCorrectionVatAdjustmentEvent,
    reversed_by: int,
) -> JournalEntry | None:
    if reversed_by <= 0:
        raise PurchaseValueCorrectionVatAdjustmentJournalSourceStateError(
            "reversed_by must be greater than zero"
        )

    amount = _validate_event(reversal_event)

    if reversal_event.reversal_of_id is None:
        raise PurchaseValueCorrectionVatAdjustmentJournalSourceStateError(
            "Only a PVC VAT reversal event can reverse accounting"
        )

    if amount == ZERO:
        unexpected = (
            await db.execute(
                select(JournalEntry.id).where(
                    JournalEntry.company_id
                    == reversal_event.company_id,
                    JournalEntry.purchase_value_correction_vat_adjustment_event_id
                    == reversal_event.reversal_of_id,
                    JournalEntry.reversal_of_id.is_(None),
                )
            )
        ).scalar_one_or_none()

        if unexpected is not None:
            raise PurchaseValueCorrectionVatAdjustmentJournalSourceStateError(
                "Zero-tax PVC VAT adjustment unexpectedly "
                "has an original JournalEntry"
            )

        return None

    original = (
        await get_original_purchase_value_correction_vat_adjustment_journal_entry(
            db,
            company_id=reversal_event.company_id,
            purchase_value_correction_vat_adjustment_event_id=(
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
            purchase_value_correction_vat_adjustment_event_id_override=(
                reversal_event.id
            ),
        )
    except AccountingReversalError as exc:
        raise PurchaseValueCorrectionVatAdjustmentJournalError(
            str(exc)
        ) from exc
