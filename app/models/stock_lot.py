from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class StockLot(Base):
    __tablename__ = "stock_lots"

    __table_args__ = (
        UniqueConstraint(
            "source_document_line_id",
            name="uq_stock_lot_source_document_line",
        ),
        CheckConstraint(
            "original_quantity > 0",
            name="ck_stock_lot_original_quantity_positive",
        ),
        CheckConstraint(
            "remaining_quantity >= 0",
            name="ck_stock_lot_remaining_quantity_nonnegative",
        ),
        CheckConstraint(
            "remaining_quantity <= original_quantity",
            name="ck_stock_lot_remaining_not_above_original",
        ),
        CheckConstraint(
            "unit_cost >= 0",
            name="ck_stock_lot_unit_cost_nonnegative",
        ),
        Index(
            "ix_stock_lot_fifo_lookup",
            "company_id",
            "product_id",
            "warehouse_id",
            "received_date",
            "id",
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

    product_id: Mapped[int] = mapped_column(
        ForeignKey(
            "products.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )

    warehouse_id: Mapped[int] = mapped_column(
        ForeignKey(
            "warehouses.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )

    source_document_id: Mapped[int] = mapped_column(
        ForeignKey(
            "documents.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
        index=True,
    )

    source_document_line_id: Mapped[int] = mapped_column(
        ForeignKey(
            "document_lines.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
        
    )

    received_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
        index=True,
    )

    original_quantity: Mapped[Decimal] = mapped_column(
        Numeric(18, 4),
        nullable=False,
    )

    remaining_quantity: Mapped[Decimal] = mapped_column(
        Numeric(18, 4),
        nullable=False,
    )

    unit_cost: Mapped[Decimal] = mapped_column(
        Numeric(18, 4),
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )