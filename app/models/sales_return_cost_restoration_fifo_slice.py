from datetime import (
    datetime,
    timezone,
)
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Numeric,
    UniqueConstraint,
)
from sqlalchemy.orm import (
    Mapped,
    mapped_column,
)

from app.core.database import Base


class SalesReturnCostRestorationFifoSlice(Base):
    """
    Immutable granular FIFO provenance belonging to one
    SalesReturnCostRestorationEvent.

    One row identifies the exact portion of one original
    StockLotConsumption restored by the parent event.

    Multiple rows may belong to one parent event.

    These rows are provenance facts. They are NOT physical
    StockLot rows.
    """

    __tablename__ = (
        "sales_return_cost_restoration_fifo_slices"
    )

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "id",
            name=(
                "uq_srcfs_company_id_id"
            ),
        ),
        UniqueConstraint(
            "sales_return_cost_restoration_event_id",
            "fifo_consumption_id",
            name=(
                "uq_srcfs_event_consumption"
            ),
        ),
        ForeignKeyConstraint(
            [
                "company_id",
                (
                    "sales_return_cost_restoration_"
                    "event_id"
                ),
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
            ],
            name=(
                "fk_srcfs_parent_event"
            ),
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "restored_quantity > 0",
            name=(
                "ck_srcfs_quantity_positive"
            ),
        ),
        CheckConstraint(
            "historical_unit_cost >= 0",
            name=(
                "ck_srcfs_unit_cost_nonnegative"
            ),
        ),
        CheckConstraint(
            "restored_valuation_amount >= 0",
            name=(
                "ck_srcfs_valuation_nonnegative"
            ),
        ),
        Index(
            "ix_srcfs_fifo_provenance",
            "company_id",
            "fifo_consumption_id",
            (
                "sales_return_cost_restoration_"
                "event_id"
            ),
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

    sales_return_cost_restoration_event_id: Mapped[
        int
    ] = mapped_column(
        nullable=False,
        index=True,
    )

    fifo_consumption_id: Mapped[int] = (
        mapped_column(
            ForeignKey(
                "stock_lot_consumptions.id",
                ondelete="RESTRICT",
            ),
            nullable=False,
            index=True,
        )
    )

    stock_lot_id: Mapped[int] = mapped_column(
        ForeignKey(
            "stock_lots.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
        index=True,
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

    historical_unit_cost: Mapped[Decimal] = (
        mapped_column(
            Numeric(
                24,
                8,
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

    created_at: Mapped[datetime] = mapped_column(
        DateTime(
            timezone=True
        ),
        nullable=False,
        default=lambda: datetime.now(
            timezone.utc
        ),
    )
