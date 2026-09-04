from datetime import (
    date,
    datetime,
    timezone,
)
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import (
    Mapped,
    mapped_column,
)

from app.core.database import Base


class SalesReturnCostRestorationEvent(Base):
    """
    Immutable historical-cost restoration produced by one
    TradeReturnEvent against one original InventoryCostEntry.

    This event represents cost truth only.

    It does NOT itself mutate:
    - StockLot;
    - StockBalance;
    - MovingAverageBalance;
    - StockLedger;
    - JournalEntry.

    valuation_method snapshots the original ISSUE costing method.

    FIFO granular provenance is stored separately in
    SalesReturnCostRestorationFifoSlice.
    """

    __tablename__ = (
        "sales_return_cost_restoration_events"
    )

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "id",
            name=(
                "uq_srcre_event_company_id_id"
            ),
        ),
        UniqueConstraint(
            "company_id",
            "id",
            "trade_return_event_id",
            "inventory_cost_entry_id",
            name=(
                "uq_srcre_event_company_id_id_source"
            ),
        ),
        UniqueConstraint(
            "reversal_of_id",
            name=(
                "uq_srcre_event_reversal_of_id"
            ),
        ),
        ForeignKeyConstraint(
            [
                "company_id",
                "trade_return_event_id",
            ],
            [
                "trade_return_events.company_id",
                "trade_return_events.id",
            ],
            name=(
                "fk_srcre_event_trade_return"
            ),
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "company_id",
                "reversal_of_id",
                "trade_return_event_id",
                "inventory_cost_entry_id",
            ],
            [
                (
                    "sales_return_cost_restoration_events."
                    "company_id"
                ),
                (
                    "sales_return_cost_restoration_events."
                    "id"
                ),
                (
                    "sales_return_cost_restoration_events."
                    "trade_return_event_id"
                ),
                (
                    "sales_return_cost_restoration_events."
                    "inventory_cost_entry_id"
                ),
            ],
            name=(
                "fk_srcre_event_reversal_source"
            ),
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            (
                "valuation_method IN "
                "('fifo', 'weighted_average_moving')"
            ),
            name=(
                "ck_srcre_event_valuation_method"
            ),
        ),
        CheckConstraint(
            "restored_quantity > 0",
            name=(
                "ck_srcre_event_quantity_positive"
            ),
        ),
        CheckConstraint(
            "restored_valuation_amount >= 0",
            name=(
                "ck_srcre_event_valuation_nonnegative"
            ),
        ),
        CheckConstraint(
            "restored_cost_amount >= 0",
            name=(
                "ck_srcre_event_cost_nonnegative"
            ),
        ),
        CheckConstraint(
            "aggregate_historical_unit_cost >= 0",
            name=(
                "ck_srcre_event_unit_cost_nonnegative"
            ),
        ),
        CheckConstraint(
            (
                "reversal_of_id IS NULL "
                "OR reversal_of_id <> id"
            ),
            name=(
                "ck_srcre_event_not_self_reversal"
            ),
        ),
        Index(
            "ix_srcre_event_pair_history",
            "company_id",
            "trade_return_event_id",
            "inventory_cost_entry_id",
            "id",
            unique=False,
        ),
    )

    id: Mapped[int] = mapped_column(
        primary_key=True,
    )

    company_id: Mapped[int] = mapped_column(
        ForeignKey(
            "companies.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
        index=True,
    )

    trade_return_event_id: Mapped[int] = (
        mapped_column(
            nullable=False,
            index=True,
        )
    )

    inventory_cost_entry_id: Mapped[int] = (
        mapped_column(
            ForeignKey(
                "inventory_cost_entries.id",
                ondelete="RESTRICT",
            ),
            nullable=False,
            index=True,
        )
    )

    restoration_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
        index=True,
    )

    valuation_method: Mapped[str] = mapped_column(
        String(
            32
        ),
        nullable=False,
    )

    restored_quantity: Mapped[Decimal] = (
        mapped_column(
            Numeric(
                18,
                4,
            ),
            nullable=False,
        )
    )

    restored_valuation_amount: Mapped[Decimal] = (
        mapped_column(
            Numeric(
                24,
                8,
            ),
            nullable=False,
        )
    )

    restored_cost_amount: Mapped[Decimal] = (
        mapped_column(
            Numeric(
                18,
                2,
            ),
            nullable=False,
        )
    )

    aggregate_historical_unit_cost: Mapped[
        Decimal
    ] = mapped_column(
        Numeric(
            24,
            8,
        ),
        nullable=False,
    )

    created_by: Mapped[int] = mapped_column(
        ForeignKey(
            "users.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(
            timezone=True
        ),
        nullable=False,
        default=lambda: datetime.now(
            timezone.utc
        ),
    )

    reversal_of_id: Mapped[
        int | None
    ] = mapped_column(
        nullable=True,
        index=True,
    )
