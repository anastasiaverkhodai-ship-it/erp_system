from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.account import Account
from app.models.company import Company
from app.models.journal_entry import JournalEntry, JournalEntryStatus
from app.models.journal_entry_line import JournalEntryLine
from app.models.opening_balance import OpeningBalance
from app.schemas.opening_balance import OpeningBalanceCreate
from app.services.accounting_posting import post_journal_entry
from app.services.accounting_reversal import reverse_journal_entry
from app.services.idempotency_fingerprint_service import generate_request_fingerprint


class OpeningBalanceError(Exception):
    pass


class OpeningBalanceNotFoundError(OpeningBalanceError):
    pass


async def _validate_accounts(
    db: AsyncSession,
    company_id: int,
    account_ids: set[int],
) -> None:
    rows = (
        await db.execute(
            select(Account.id).where(
                Account.id.in_(account_ids),
                Account.company_id == company_id,
                Account.is_active.is_(True),
                Account.is_postable.is_(True),
            )
        )
    ).scalars().all()

    if set(rows) != account_ids:
        raise OpeningBalanceError(
            "Opening balance contains an invalid, inactive, "
            "non-postable, or foreign-company account"
        )


def _validate_balance(
    data: OpeningBalanceCreate,
) -> None:
    debit = sum(
        (line.debit for line in data.lines),
        Decimal("0"),
    )
    credit = sum(
        (line.credit for line in data.lines),
        Decimal("0"),
    )

    if debit <= 0:
        raise OpeningBalanceError(
            "Opening balance total must be greater than zero"
        )

    if debit != credit:
        raise OpeningBalanceError(
            "Opening balance is not balanced: "
            f"debit={debit}, credit={credit}"
        )


async def create_opening_balance(
    db: AsyncSession,
    company_id: int,
    created_by: int,
    data: OpeningBalanceCreate,
) -> OpeningBalance:
    if company_id <= 0 or created_by <= 0:
        raise OpeningBalanceError("Invalid company or actor")
    if data.opening_date > date.today():
        raise OpeningBalanceError("Future opening balance is not supported")
    # Company lock serializes retries before any journal is inserted.
    company = await db.scalar(
        select(Company).where(Company.id == company_id).with_for_update()
    )
    if company is None:
        raise OpeningBalanceNotFoundError("Company not found")
    fingerprint = generate_request_fingerprint(
        data.model_dump(mode="json", exclude={"request_key"})
    )
    existing = await db.scalar(
        select(OpeningBalance).where(
            OpeningBalance.company_id == company_id,
            OpeningBalance.request_key == data.request_key,
        )
    )
    if existing is not None:
        if existing.request_fingerprint != fingerprint:
            raise OpeningBalanceError("Request key already used for different opening balance data")
        return existing
    _validate_balance(data)

    await _validate_accounts(
        db=db,
        company_id=company_id,
        account_ids={
            line.account_id
            for line in data.lines
        },
    )

    journal = JournalEntry(
        company_id=company_id,
        entry_date=data.opening_date,
        description=data.description,
        status=JournalEntryStatus.DRAFT,
        created_by=created_by,
    )
    db.add(journal)
    await db.flush()

    opening = OpeningBalance(
        company_id=company_id,
        request_key=data.request_key,
        request_fingerprint=fingerprint,
        opening_date=data.opening_date,
        description=data.description,
        journal_entry_id=journal.id,
        created_by=created_by,
    )
    db.add(opening)
    await db.flush()

    journal.opening_balance_id = opening.id

    for line_no, line in enumerate(
        data.lines,
        start=1,
    ):
        db.add(
            JournalEntryLine(
                journal_entry_id=journal.id,
                line_no=line_no,
                account_id=line.account_id,
                debit=line.debit,
                credit=line.credit,
                description=line.description,
            )
        )

    await db.flush()

    return opening


async def get_opening_balance(
    db: AsyncSession,
    company_id: int,
    opening_balance_id: int,
) -> OpeningBalance:
    result = await db.execute(
        select(OpeningBalance)
        .where(
            OpeningBalance.id == opening_balance_id,
            OpeningBalance.company_id == company_id,
        )
        .with_for_update()
    )

    opening = result.scalar_one_or_none()

    if opening is None:
        raise OpeningBalanceNotFoundError(
            "Opening balance not found"
        )

    return opening


async def post_opening_balance(
    db: AsyncSession,
    company_id: int,
    opening_balance_id: int,
) -> JournalEntry:
    opening = await get_opening_balance(
        db=db,
        company_id=company_id,
        opening_balance_id=opening_balance_id,
    )

    journal = await db.scalar(
        select(JournalEntry).where(
            JournalEntry.company_id == company_id,
            JournalEntry.id == opening.journal_entry_id,
        ).with_for_update().execution_options(populate_existing=True)
    )
    if journal.status == JournalEntryStatus.POSTED:
        return journal
    return await post_journal_entry(
        db=db,
        company_id=company_id,
        journal_entry_id=opening.journal_entry_id,
    )


from app.services.landed_cost_inventory_lifecycle import landed_cost_inventory_operation


@landed_cost_inventory_operation(date_argument="reversal_date")
async def reverse_opening_balance(
    db: AsyncSession,
    company_id: int,
    opening_balance_id: int,
    reversal_date: date,
    reversed_by: int,
) -> JournalEntry:
    opening = await get_opening_balance(
        db=db,
        company_id=company_id,
        opening_balance_id=opening_balance_id,
    )

    if reversed_by <= 0 or not opening.opening_date <= reversal_date <= date.today():
        raise OpeningBalanceError("Invalid opening reversal actor/date")
    # Lock debt sources before the journal: settlement owns an item lock while
    # posting its clearing. This prevents reversal racing an active allocation.
    from app.models.counterparty_open_item import CounterpartyOpenItem
    await db.execute(select(CounterpartyOpenItem.id).where(
        CounterpartyOpenItem.company_id==company_id,
        CounterpartyOpenItem.opening_balance_id==opening.id).order_by(CounterpartyOpenItem.id).with_for_update())
    # Lock the same journal as the generic reversal endpoint before replay lookup.
    await db.scalar(
        select(JournalEntry).where(
            JournalEntry.company_id == company_id,
            JournalEntry.id == opening.journal_entry_id,
        ).with_for_update().execution_options(populate_existing=True)
    )
    existing = await db.scalar(
        select(JournalEntry).where(
            JournalEntry.company_id == company_id,
            JournalEntry.reversal_of_id == opening.journal_entry_id,
        )
    )
    if existing is not None:
        if existing.entry_date != reversal_date:
            raise OpeningBalanceError("Opening balance was already reversed on another date")
        return existing
    from app.services.opening_balance_detail_service import reverse_opening_detail
    previous=db.info.get('opening_detail_lifecycle')
    db.info['opening_detail_lifecycle']=opening.id
    try:
        await reverse_opening_detail(db, company_id=company_id, opening_balance_id=opening.id,
            reversal_date=reversal_date, reversed_by=reversed_by)
        return await reverse_journal_entry(db=db, company_id=company_id,
            journal_entry_id=opening.journal_entry_id, reversal_date=reversal_date, reversed_by=reversed_by)
    finally:
        if previous is None:
            db.info.pop('opening_detail_lifecycle', None)
        else:
            db.info['opening_detail_lifecycle']=previous


async def load_opening_balance_response_data(
    db: AsyncSession,
    company_id: int,
    opening_balance_id: int,
):
    opening = (
        await db.execute(
            select(OpeningBalance).where(
                OpeningBalance.id == opening_balance_id,
                OpeningBalance.company_id == company_id,
            )
        )
    ).scalar_one_or_none()

    if opening is None:
        raise OpeningBalanceNotFoundError(
            "Opening balance not found"
        )

    journal = (
        await db.execute(
            select(JournalEntry)
            .options(
                selectinload(JournalEntry.lines)
            )
            .where(
                JournalEntry.id == opening.journal_entry_id,
                JournalEntry.company_id == company_id,
            )
        )
    ).scalar_one()

    reversal_id = (
        await db.execute(
            select(JournalEntry.id).where(
                JournalEntry.company_id == company_id,
                JournalEntry.reversal_of_id == journal.id,
            )
        )
    ).scalar_one_or_none()

    return opening, journal, reversal_id
