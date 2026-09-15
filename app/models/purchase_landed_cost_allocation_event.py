from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class PurchaseLandedCostAllocationEvent(Base):
    """
    Immutable allocation of one landed-cost source event to one
    canonical purchase fulfillment line / receipt line.

    Allocation is quantity-neutral and preserves exact receipt
    provenance for later FIFO / moving-average propagation.
    """

    __tablename__ = "purchase_landed_cost_allocation_events"

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "landed_cost_event_id",
            "trade_fulfillment_line_id",
            name="uq_plca_event_fulfillment_line",
        ),
        ForeignKeyConstraint(
            (
                "company_id",
                "landed_cost_event_id",
            ),
            (
                "purchase_landed_cost_events.company_id",
                "purchase_landed_cost_events.id",
            ),
            name="fk_plca_landed_cost_event",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ("trade_fulfillment_line_id",),
            ("trade_fulfillment_lines.id",),
            name="fk_plca_fulfillment_line",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "allocated_amount > 0",
            name="ck_plca_amount_positive",
        ),
        CheckConstraint(
            "quantity > 0",
            name="ck_plca_quantity_positive",
        ),
        Index(
            "ix_plca_company",
            "company_id",
        ),
        Index(
            "ix_plca_event",
            "landed_cost_event_id",
        ),
        Index(
            "ix_plca_fline",
            "trade_fulfillment_line_id",
        ),
        Index(
            "ix_plca_trade_doc",
            "trade_document_id",
        ),
        Index(
            "ix_plca_trade_line",
            "trade_document_line_id",
        ),
        Index(
            "ix_plca_wh_line",
            "warehouse_document_line_id",
        ),
        Index(
            "ix_plca_product",
            "product_id",
        ),
        Index(
            "ix_plca_warehouse",
            "warehouse_id",
        ),
        Index(
            "ix_plca_source",
            "company_id",
            "landed_cost_event_id",
            "id",
        ),
        Index(
            "ix_plca_fulfillment",
            "company_id",
            "trade_fulfillment_line_id",
            "id",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    company_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    landed_cost_event_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    trade_fulfillment_line_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    trade_document_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    trade_document_line_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    warehouse_document_line_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    product_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    warehouse_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    quantity: Mapped[Decimal] = mapped_column(
        Numeric(18, 4),
        nullable=False,
    )

    allocated_amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
