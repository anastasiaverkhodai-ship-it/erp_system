from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.journal_entry import (
    JournalEntry,
    JournalEntryStatus,
)
from app.models.journal_entry_line import (
    JournalEntryLine,
)
from app.models.purchase_value_correction_moving_average_replay_event import (
    PurchaseValueCorrectionMovingAverageReplayEvent,
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
from app.services.purchase_value_correction_moving_average_accounting_service import (
    PurchaseValueCorrectionMovingAverageAccountingError,
    PurchaseValueCorrectionMovingAverageAccountingPlan,
    create_purchase_value_correction_moving_average_accounting_plan,
    required_roles_for_purchase_value_correction_moving_average_plan,
)
from app.services.purchase_value_correction_moving_average_issued_destination_resolver import (
    HistoricalMovingAverageIssuedDestination,
    PurchaseValueCorrectionMovingAverageIssuedDestinationError,
    resolve_purchase_value_correction_moving_average_issued_destination,
)


class PurchaseValueCorrectionMovingAverageJournalError(
    Exception
):
    """Base PVC moving-average JournalEntry error."""


class PurchaseValueCorrectionMovingAverageJournalSourceStateError(
    PurchaseValueCorrectionMovingAverageJournalError
):
    """PVC moving-average replay event is invalid for this GL action."""


class PurchaseValueCorrectionMovingAverageJournalDuplicateError(
    PurchaseValueCorrectionMovingAverageJournalError
):
    """A JournalEntry already exists for this immutable replay event."""


class PurchaseValueCorrectionMovingAverageJournalNotFoundError(
    PurchaseValueCorrectionMovingAverageJournalError
):
    """Required original PVC moving-average JournalEntry was not found."""


class PurchaseValueCorrectionMovingAverageJournalCurrencyError(
    PurchaseValueCorrectionMovingAverageJournalError
):
    """PVC moving-average accounting currency is unsupported."""


def _positive_int(
    value,
    *,
    field: str,
) -> int:
    if (
        not isinstance(
            value,
            int,
        )
        or isinstance(
            value,
            bool,
        )
        or value <= 0
    ):
        raise PurchaseValueCorrectionMovingAverageJournalSourceStateError(
            f"{field} must be greater than zero"
        )

    return value


def _validate_event_identity(
    event: PurchaseValueCorrectionMovingAverageReplayEvent,
) -> None:
    _positive_int(
        event.id,
        field="PVC MA replay event id",
    )
    _positive_int(
        event.company_id,
        field="PVC MA replay event company_id",
    )


def validate_purchase_value_correction_moving_average_accounting_currency(
    event: PurchaseValueCorrectionMovingAverageReplayEvent,
) -> None:
    if event.currency_code != "UAH":
        raise PurchaseValueCorrectionMovingAverageJournalCurrencyError(
            "PVC moving-average accounting currently supports UAH only"
        )


async def _build_journal_lines(
    db: AsyncSession,
    *,
    event: PurchaseValueCorrectionMovingAverageReplayEvent,
    plan: PurchaseValueCorrectionMovingAverageAccountingPlan,
    description: str,
) -> list[
    JournalEntryLine
]:
    try:
        accounts = await resolve_company_account_roles(
            db,
            company_id=event.company_id,
            roles=(
                required_roles_for_purchase_value_correction_moving_average_plan(
                    plan
                )
            ),
        )
    except AccountingAccountRoleResolutionError as exc:
        raise PurchaseValueCorrectionMovingAverageJournalError(
            str(
                exc
            )
        ) from exc

    historical_destination: (
        HistoricalMovingAverageIssuedDestination | None
    ) = None

    if plan.destination_kind == "issued":
        try:
            historical_destination = (
                await resolve_purchase_value_correction_moving_average_issued_destination(
                    db,
                    event=event,
                    lock=True,
                )
            )
        except (
            PurchaseValueCorrectionMovingAverageIssuedDestinationError
        ) as exc:
            raise PurchaseValueCorrectionMovingAverageJournalError(
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
                raise PurchaseValueCorrectionMovingAverageJournalError(
                    "Required accounting role was not resolved"
                )

            account_id = account.id

        elif planned.destination_kind == "issued":
            if historical_destination is None:
                raise PurchaseValueCorrectionMovingAverageJournalError(
                    "Historical issued destination was not resolved"
                )

            account_id = historical_destination.account_id

        else:
            raise PurchaseValueCorrectionMovingAverageJournalError(
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
        raise PurchaseValueCorrectionMovingAverageJournalError(
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
        raise PurchaseValueCorrectionMovingAverageJournalError(
            str(
                exc
            )
        ) from exc


async def generate_and_post_purchase_value_correction_moving_average_journal_entry(
    db: AsyncSession,
    *,
    event: PurchaseValueCorrectionMovingAverageReplayEvent,
    created_by: int,
) -> JournalEntry:
    """
    Post one ORIGINAL immutable PVC moving-average replay event.

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

    MovingAverageMovement, MovingAverageBalance and InventoryCostEntry
    remain untouched.

    Caller owns COMMIT / ROLLBACK.
    """

    _positive_int(
        created_by,
        field="created_by",
    )

    _validate_event_identity(
        event
    )

    if event.reversal_of_id is not None:
        raise (
            PurchaseValueCorrectionMovingAverageJournalSourceStateError(
                "PVC moving-average reversal event cannot generate "
                "an original journal"
            )
        )

    validate_purchase_value_correction_moving_average_accounting_currency(
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
                    .purchase_value_correction_ma_replay_event_id
                    == event.id
                ),
                JournalEntry.reversal_of_id.is_(
                    None
                ),
            )
        )
    ).scalar_one_or_none()

    if existing_id is not None:
        raise PurchaseValueCorrectionMovingAverageJournalDuplicateError(
            "Original JournalEntry already exists for "
            "PVC moving-average replay event"
        )

    try:
        plan = (
            create_purchase_value_correction_moving_average_accounting_plan(
                original_valuation_amount=(
                    event.original_valuation_amount
                ),
                corrected_valuation_amount=(
                    event.corrected_valuation_amount
                ),
                destination_kind=(
                    event.effect_kind
                ),
            )
        )
    except PurchaseValueCorrectionMovingAverageAccountingError as exc:
        raise PurchaseValueCorrectionMovingAverageJournalError(
            str(
                exc
            )
        ) from exc

    description = (
        "Purchase Value Correction MA replay "
        f"{event.id} / {event.effect_kind}"
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
        purchase_value_correction_fifo_impact_event_id=None,
        purchase_value_correction_ma_replay_event_id=event.id,
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


async def get_original_purchase_value_correction_moving_average_journal_entry(
    db: AsyncSession,
    *,
    company_id: int,
    replay_event_id: int,
    lock: bool = False,
) -> JournalEntry:
    _positive_int(
        company_id,
        field="company_id",
    )
    _positive_int(
        replay_event_id,
        field="replay_event_id",
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
                .purchase_value_correction_ma_replay_event_id
                == replay_event_id
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
        raise PurchaseValueCorrectionMovingAverageJournalNotFoundError(
            "Original PVC moving-average JournalEntry was not found"
        )

    return entry


async def reverse_purchase_value_correction_moving_average_journal_entry(
    db: AsyncSession,
    *,
    reversal_event: PurchaseValueCorrectionMovingAverageReplayEvent,
    reversed_by: int,
) -> JournalEntry:
    """
    Reverse one original PVC moving-average JournalEntry exactly.

    The original JournalEntry accounts are mirrored directly.
    Historical issued destination is NOT resolved again.

    The reversal JournalEntry is typed by the immutable MA replay
    reversal event through the dedicated JournalEntry source column.

    Caller owns COMMIT / ROLLBACK.
    """

    _positive_int(
        reversed_by,
        field="reversed_by",
    )

    _validate_event_identity(
        reversal_event
    )

    if reversal_event.reversal_of_id is None:
        raise (
            PurchaseValueCorrectionMovingAverageJournalSourceStateError(
                "Only a PVC moving-average reversal event can reverse "
                "PVC moving-average accounting"
            )
        )

    _positive_int(
        reversal_event.reversal_of_id,
        field="PVC MA reversal_of_id",
    )

    validate_purchase_value_correction_moving_average_accounting_currency(
        reversal_event
    )

    existing_id = (
        await db.execute(
            select(
                JournalEntry.id
            ).where(
                JournalEntry.company_id
                == reversal_event.company_id,
                (
                    JournalEntry
                    .purchase_value_correction_ma_replay_event_id
                    == reversal_event.id
                ),
            )
        )
    ).scalar_one_or_none()

    if existing_id is not None:
        raise PurchaseValueCorrectionMovingAverageJournalDuplicateError(
            "JournalEntry already exists for PVC moving-average "
            "reversal event"
        )

    original = (
        await get_original_purchase_value_correction_moving_average_journal_entry(
            db,
            company_id=reversal_event.company_id,
            replay_event_id=reversal_event.reversal_of_id,
            lock=True,
        )
    )

    if original.status != JournalEntryStatus.POSTED:
        raise PurchaseValueCorrectionMovingAverageJournalSourceStateError(
            "Original PVC moving-average JournalEntry is not POSTED"
        )

    try:
        return await reverse_journal_entry(
            db=db,
            company_id=reversal_event.company_id,
            journal_entry_id=original.id,
            reversal_date=reversal_event.recognition_date,
            reversed_by=reversed_by,
            purchase_value_correction_ma_replay_event_id_override=(
                reversal_event.id
            ),
        )
    except AccountingReversalError as exc:
        raise PurchaseValueCorrectionMovingAverageJournalError(
            str(
                exc
            )
        ) from exc
