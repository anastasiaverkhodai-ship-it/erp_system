from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.counterparty_open_item import (
    CounterpartyOpenItem,
    CounterpartyOpenItemType,
)
from app.models.payment_settlement_allocation import (
    PaymentSettlementAllocation,
)
from app.models.trade_document import TradeDocument
from app.services.sales_ar_aging_calculation_service import (
    HistoricalSettlement,
    SalesARAgingProjection,
    calculate_sales_ar_aging_projection,
)


class SalesARAgingProjectionError(ValueError):
    pass


@dataclass(frozen=True)
class SalesARAgingOpenItemProjection:
    open_item_id: int
    counterparty_id: int
    trade_document_id: int | None
    currency_code: str
    document_date: date
    projection: SalesARAgingProjection


def _positive_int(value: int, *, label: str) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value <= 0
    ):
        raise SalesARAgingProjectionError(
            f"{label} must be a positive integer"
        )
    return value


def _currency(value: str) -> str:
    if not isinstance(value, str):
        raise SalesARAgingProjectionError(
            "currency_code must be a string"
        )

    value = value.strip().upper()

    if len(value) != 3 or not value.isalpha():
        raise SalesARAgingProjectionError(
            "currency_code must be a 3-letter code"
        )

    return value


async def load_sales_ar_aging_projection(
    db: AsyncSession,
    *,
    company_id: int,
    as_of_date: date,
    counterparty_id: int | None = None,
    item_type: CounterpartyOpenItemType = CounterpartyOpenItemType.RECEIVABLE,
) -> tuple[SalesARAgingOpenItemProjection, ...]:
    """
    Read-only historical Sales AR aging projection.

    Historical commercial balance is reconstructed from:
      * immutable CounterpartyOpenItem principal/due-date snapshot;
      * invoice cancellation chronology;
      * complete PaymentSettlementAllocation history.

    Current Open Item / settlement statuses are deliberately not
    used as historical truth.

    CustomerAdvanceClearingEvent is deliberately not subtracted:
    commercial settlement already reduces the receivable and
    subtracting clearing again would double count AR reduction.

    No FX conversion is performed.
    """
    if item_type not in (CounterpartyOpenItemType.RECEIVABLE, CounterpartyOpenItemType.PAYABLE):
        raise SalesARAgingProjectionError("Unsupported open-item type")

    company_id = _positive_int(
        company_id,
        label="company_id",
    )

    if not isinstance(as_of_date, date):
        raise SalesARAgingProjectionError(
            "as_of_date must be a date"
        )

    if counterparty_id is not None:
        counterparty_id = _positive_int(
            counterparty_id,
            label="counterparty_id",
        )

    predicates = [
        CounterpartyOpenItem.company_id == company_id,
        CounterpartyOpenItem.item_type
        == item_type,
        CounterpartyOpenItem.document_date <= as_of_date,
        TradeDocument.company_id == company_id,
        TradeDocument.id
        == CounterpartyOpenItem.trade_document_id,
    ]

    if counterparty_id is not None:
        predicates.append(
            CounterpartyOpenItem.counterparty_id
            == counterparty_id
        )

    rows = (
        await db.execute(
            select(
                CounterpartyOpenItem,
                TradeDocument,
            )
            .join(
                TradeDocument,
                (
                    TradeDocument.company_id
                    == CounterpartyOpenItem.company_id
                )
                & (
                    TradeDocument.id
                    == CounterpartyOpenItem.trade_document_id
                ),
            )
            .where(*predicates)
            .order_by(
                CounterpartyOpenItem.document_date,
                CounterpartyOpenItem.id,
            )
        )
    ).all()

    eligible: list[
        tuple[CounterpartyOpenItem, TradeDocument]
    ] = []

    for open_item, document in rows:
        if open_item.company_id != company_id:
            raise SalesARAgingProjectionError(
                "Open Item company differs from projection company"
            )

        if document.company_id != company_id:
            raise SalesARAgingProjectionError(
                "Trade Document company differs from projection company"
            )

        if document.id != open_item.trade_document_id:
            raise SalesARAgingProjectionError(
                "Trade Document provenance differs from Open Item"
            )

        if open_item.item_type != item_type:
            raise SalesARAgingProjectionError(
                "Projection contains a different Open Item type"
            )

        # Cancellation is effective for the closing balance on its
        # calendar date. Before that date the obligation remains part
        # of historical AR.
        if (
            document.cancelled_at is not None
            and document.cancelled_at.date() <= as_of_date
        ):
            continue

        eligible.append(
            (open_item, document)
        )

    from app.models.opening_balance import OpeningBalance
    from app.models.journal_entry import JournalEntry
    from sqlalchemy.orm import aliased
    reversal=aliased(JournalEntry)
    opening_items=(await db.scalars(select(CounterpartyOpenItem)
        .join(OpeningBalance, (OpeningBalance.company_id==CounterpartyOpenItem.company_id)
            & (OpeningBalance.id==CounterpartyOpenItem.opening_balance_id))
        .join(JournalEntry, (JournalEntry.company_id==company_id)
            & (JournalEntry.id==OpeningBalance.journal_entry_id))
        .where(CounterpartyOpenItem.company_id==company_id,CounterpartyOpenItem.item_type==item_type,
            OpeningBalance.opening_date<=as_of_date, JournalEntry.status.in_(('posted','reversed')),
            ~select(reversal.id).where(reversal.company_id==company_id,
                reversal.reversal_of_id==JournalEntry.id,reversal.entry_date<=as_of_date).exists(),
            *([CounterpartyOpenItem.counterparty_id==counterparty_id] if counterparty_id is not None else []))
        .order_by(CounterpartyOpenItem.document_date,CounterpartyOpenItem.id))).all()
    eligible.extend((item,None) for item in opening_items)

    if not eligible:
        return ()

    open_item_ids = tuple(
        open_item.id
        for open_item, _ in eligible
    )

    allocation_rows = (
        (
            await db.execute(
                select(
                    PaymentSettlementAllocation
                )
                .where(
                    PaymentSettlementAllocation.company_id
                    == company_id,
                    PaymentSettlementAllocation.open_item_id.in_(
                        open_item_ids
                    ),
                )
                .order_by(
                    PaymentSettlementAllocation.open_item_id,
                    PaymentSettlementAllocation.created_at,
                    PaymentSettlementAllocation.id,
                )
            )
        )
        .scalars()
        .all()
    )

    settlements_by_open_item: dict[
        int,
        list[HistoricalSettlement],
    ] = defaultdict(list)

    eligible_ids = set(open_item_ids)

    for allocation in allocation_rows:
        if allocation.company_id != company_id:
            raise SalesARAgingProjectionError(
                "Settlement company differs from projection company"
            )

        if allocation.open_item_id not in eligible_ids:
            raise SalesARAgingProjectionError(
                "Settlement references Open Item outside projection"
            )

        settlements_by_open_item[
            allocation.open_item_id
        ].append(
            HistoricalSettlement(
                amount=Decimal(allocation.amount),
                created_at=allocation.created_at,
                reversed_at=allocation.reversed_at,
            )
        )

    projections = []

    for open_item, _document in eligible:
        projection = calculate_sales_ar_aging_projection(
            original_amount=Decimal(
                open_item.original_amount
            ),
            due_date=open_item.due_date,
            as_of_date=as_of_date,
            settlements=settlements_by_open_item.get(
                open_item.id,
                (),
            ),
        )

        # Fully settled items have no closing AR balance and therefore
        # do not belong to the aging population.
        if projection.open_amount == Decimal("0.00"):
            continue

        projections.append(
            SalesARAgingOpenItemProjection(
                open_item_id=_positive_int(
                    open_item.id,
                    label="open_item_id",
                ),
                counterparty_id=_positive_int(
                    open_item.counterparty_id,
                    label="counterparty_id",
                ),
                trade_document_id=(_positive_int(open_item.trade_document_id, label="trade_document_id")
                    if open_item.trade_document_id is not None else None),
                currency_code=_currency(
                    open_item.currency_code
                ),
                document_date=open_item.document_date,
                projection=projection,
            )
        )

    return tuple(projections)
