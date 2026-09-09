from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.accounting_reversal import (
    AccountingReversalError,
    reverse_journal_entry,
)

from app.models.journal_entry import (
    JournalEntry,
    JournalEntryStatus,
)
from app.models.journal_entry_line import (
    JournalEntryLine,
)
from app.models.purchase_value_correction_fifo_impact_event import (
    PurchaseValueCorrectionFifoImpactEvent,
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
from app.services.purchase_value_correction_fifo_accounting_service import (
    PurchaseValueCorrectionFifoAccountingError,
    PurchaseValueCorrectionFifoAccountingPlan,
    create_purchase_value_correction_fifo_accounting_plan,
    required_roles_for_purchase_value_correction_fifo_plan,
)
from app.services.purchase_value_correction_fifo_issued_destination_resolver import (
    HistoricalIssuedDestination,
    PurchaseValueCorrectionFifoIssuedDestinationError,
    resolve_purchase_value_correction_fifo_issued_destination,
)


class PurchaseValueCorrectionFifoJournalError(
    Exception
):
    """Base PVC FIFO JournalEntry error."""


class PurchaseValueCorrectionFifoJournalSourceStateError(
    PurchaseValueCorrectionFifoJournalError
):
    """PVC FIFO impact is invalid for this GL action."""


class PurchaseValueCorrectionFifoJournalDuplicateError(
    PurchaseValueCorrectionFifoJournalError
):
    """A JournalEntry already exists for this immutable impact."""


class PurchaseValueCorrectionFifoJournalNotFoundError(
    PurchaseValueCorrectionFifoJournalError
):
    """Required original PVC FIFO JournalEntry was not found."""


class PurchaseValueCorrectionFifoJournalCurrencyError(
    PurchaseValueCorrectionFifoJournalError
):
    """PVC FIFO accounting currency is unsupported."""


def _validate_event_identity(
    event: PurchaseValueCorrectionFifoImpactEvent,
) -> None:
    if (
        event.id is None
        or not isinstance(
            event.id,
            int,
        )
        or isinstance(
            event.id,
            bool,
        )
        or event.id <= 0
    ):
        raise PurchaseValueCorrectionFifoJournalSourceStateError(
            "PVC FIFO impact event must have a positive persistent ID"
        )

    if (
        not isinstance(
            event.company_id,
            int,
        )
        or isinstance(
            event.company_id,
            bool,
        )
        or event.company_id <= 0
    ):
        raise PurchaseValueCorrectionFifoJournalSourceStateError(
            "PVC FIFO impact company_id must be positive"
        )


def validate_purchase_value_correction_fifo_accounting_currency(
    event: PurchaseValueCorrectionFifoImpactEvent,
) -> None:
    if event.currency_code != "UAH":
        raise PurchaseValueCorrectionFifoJournalCurrencyError(
            "PVC FIFO accounting currently supports UAH only"
        )


async def _build_journal_lines(
    db: AsyncSession,
    *,
    event: PurchaseValueCorrectionFifoImpactEvent,
    plan: PurchaseValueCorrectionFifoAccountingPlan,
    description: str,
) -> list[
    JournalEntryLine
]:
    try:
        accounts = await resolve_company_account_roles(
            db,
            company_id=event.company_id,
            roles=(
                required_roles_for_purchase_value_correction_fifo_plan(
                    plan
                )
            ),
        )
    except AccountingAccountRoleResolutionError as exc:
        raise PurchaseValueCorrectionFifoJournalError(
            str(
                exc
            )
        ) from exc

    historical_destination: (
        HistoricalIssuedDestination | None
    ) = None

    if plan.destination_kind == "issued":
        try:
            historical_destination = (
                await resolve_purchase_value_correction_fifo_issued_destination(
                    db,
                    event=event,
                    lock=True,
                )
            )
        except PurchaseValueCorrectionFifoIssuedDestinationError as exc:
            raise PurchaseValueCorrectionFifoJournalError(
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
        if planned.role is not None:
            account = accounts.get(
                planned.role
            )

            if account is None:
                raise PurchaseValueCorrectionFifoJournalError(
                    "Required accounting role was not resolved"
                )

            account_id = account.id

        elif (
            planned.destination_kind
            == "issued"
        ):
            if historical_destination is None:
                raise PurchaseValueCorrectionFifoJournalError(
                    "Historical issued destination was not resolved"
                )

            account_id = (
                historical_destination.account_id
            )

        else:
            raise PurchaseValueCorrectionFifoJournalError(
                "Accounting plan contains an unresolved destination"
            )

        lines.append(
            JournalEntryLine(
                line_no=line_no,
                account_id=account_id,
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
        raise PurchaseValueCorrectionFifoJournalError(
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
        raise PurchaseValueCorrectionFifoJournalError(
            str(
                exc
            )
        ) from exc


async def generate_and_post_purchase_value_correction_fifo_journal_entry(
    db: AsyncSession,
    *,
    event: PurchaseValueCorrectionFifoImpactEvent,
    created_by: int,
) -> JournalEntry:
    """
    Post one ORIGINAL immutable PVC FIFO impact.

    on_hand:
        delta > 0
            Dr INVENTORY_GOODS
            Cr SUPPLIER_PAYABLES

        delta < 0
            Dr SUPPLIER_PAYABLES
            Cr INVENTORY_GOODS

    issued:
        delta > 0
            Dr exact historical ISSUE cost destination
            Cr SUPPLIER_PAYABLES

        delta < 0
            Dr SUPPLIER_PAYABLES
            Cr exact historical ISSUE cost destination

    The historical issued destination is the actual account used by
    the original posted ISSUE JournalEntry, validated against the
    accounting rule attached to that ISSUE.

    No StockLot, StockLotConsumption or InventoryCostEntry mutation.

    Caller owns COMMIT / ROLLBACK.
    """

    if (
        not isinstance(
            created_by,
            int,
        )
        or isinstance(
            created_by,
            bool,
        )
        or created_by <= 0
    ):
        raise PurchaseValueCorrectionFifoJournalSourceStateError(
            "created_by must be greater than zero"
        )

    _validate_event_identity(
        event
    )

    if event.reversal_of_id is not None:
        raise PurchaseValueCorrectionFifoJournalSourceStateError(
            "PVC FIFO reversal event cannot generate an original journal"
        )

    validate_purchase_value_correction_fifo_accounting_currency(
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
                    .purchase_value_correction_fifo_impact_event_id
                    == event.id
                ),
                JournalEntry.reversal_of_id.is_(
                    None
                ),
            )
        )
    ).scalar_one_or_none()

    if existing_id is not None:
        raise PurchaseValueCorrectionFifoJournalDuplicateError(
            "Original JournalEntry already exists for PVC FIFO impact"
        )

    try:
        plan = (
            create_purchase_value_correction_fifo_accounting_plan(
                original_base_amount=(
                    event.original_base_amount
                ),
                corrected_base_amount=(
                    event.corrected_base_amount
                ),
                destination_kind=(
                    event.destination_kind
                ),
            )
        )
    except PurchaseValueCorrectionFifoAccountingError as exc:
        raise PurchaseValueCorrectionFifoJournalError(
            str(
                exc
            )
        ) from exc

    description = (
        "Purchase Value Correction FIFO impact "
        f"{event.id} / {event.destination_kind}"
    )

    lines = await _build_journal_lines(
        db,
        event=event,
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
        purchase_return_vat_adjustment_event_id=None,
        purchase_return_input_vat_credit_correction_event_id=None,
        purchase_value_correction_fifo_impact_event_id=(
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


async def get_original_purchase_value_correction_fifo_journal_entry(
    db: AsyncSession,
    *,
    company_id: int,
    impact_event_id: int,
    lock: bool = False,
) -> JournalEntry:
    if (
        not isinstance(
            company_id,
            int,
        )
        or isinstance(
            company_id,
            bool,
        )
        or company_id <= 0
    ):
        raise ValueError(
            "company_id must be greater than zero"
        )

    if (
        not isinstance(
            impact_event_id,
            int,
        )
        or isinstance(
            impact_event_id,
            bool,
        )
        or impact_event_id <= 0
    ):
        raise ValueError(
            "impact_event_id must be greater than zero"
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
                .purchase_value_correction_fifo_impact_event_id
                == impact_event_id
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
        raise PurchaseValueCorrectionFifoJournalNotFoundError(
            "Original PVC FIFO JournalEntry was not found"
        )

    return entry


async def reverse_purchase_value_correction_fifo_journal_entry(
    db: AsyncSession,
    *,
    reversal_event: PurchaseValueCorrectionFifoImpactEvent,
    reversed_by: int,
) -> JournalEntry:
    """
    Reverse one original PVC FIFO JournalEntry.

    The immutable reversal FIFO impact event identifies the historical
    original through reversal_of_id.

    The JournalEntry itself is reversed exactly:

        original lines
            -> same account_ids
            -> debit / credit mirrored

    This is critical for destination_kind='issued':
    the correction reversal must use the exact historical cost
    destination account carried by the original PVC FIFO JournalEntry.
    It must NOT resolve the current GOODS_COGS role again.

    The newly-created reversal JournalEntry is typed by
    reversal_event.id through
    purchase_value_correction_fifo_impact_event_id_override.

    Caller owns COMMIT / ROLLBACK.
    """

    if (
        not isinstance(
            reversed_by,
            int,
        )
        or isinstance(
            reversed_by,
            bool,
        )
        or reversed_by <= 0
    ):
        raise PurchaseValueCorrectionFifoJournalSourceStateError(
            "reversed_by must be greater than zero"
        )

    _validate_event_identity(
        reversal_event
    )

    if reversal_event.reversal_of_id is None:
        raise PurchaseValueCorrectionFifoJournalSourceStateError(
            "Only a PVC FIFO reversal event can reverse "
            "PVC FIFO accounting"
        )

    if (
        not isinstance(
            reversal_event.reversal_of_id,
            int,
        )
        or isinstance(
            reversal_event.reversal_of_id,
            bool,
        )
        or reversal_event.reversal_of_id <= 0
    ):
        raise PurchaseValueCorrectionFifoJournalSourceStateError(
            "PVC FIFO reversal_of_id must be greater than zero"
        )

    validate_purchase_value_correction_fifo_accounting_currency(
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
                    .purchase_value_correction_fifo_impact_event_id
                    == reversal_event.id
                ),
            )
        )
    ).scalar_one_or_none()

    if existing_reversal_id is not None:
        raise PurchaseValueCorrectionFifoJournalDuplicateError(
            "JournalEntry already exists for this "
            "PVC FIFO reversal event"
        )

    original_entry = (
        await get_original_purchase_value_correction_fifo_journal_entry(
            db,
            company_id=reversal_event.company_id,
            impact_event_id=(
                reversal_event.reversal_of_id
            ),
            lock=True,
        )
    )

    try:
        return await reverse_journal_entry(
            db=db,
            company_id=reversal_event.company_id,
            journal_entry_id=original_entry.id,
            reversal_date=(
                reversal_event.recognition_date
            ),
            reversed_by=reversed_by,
            purchase_value_correction_fifo_impact_event_id_override=(
                reversal_event.id
            ),
        )

    except AccountingReversalError as exc:
        raise PurchaseValueCorrectionFifoJournalError(
            str(
                exc
            )
        ) from exc
