from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class StockLotConsumption(Base):
    __tablename__ = "stock_lot_consumptions"

    __table_args__ = (
        UniqueConstraint(
            "issue_document_line_id",
            "stock_lot_id",
            name="uq_stock_lot_consumption_line_lot",
        ),
        CheckConstraint(
            "quantity > 0",
            name="ck_stock_lot_consumption_quantity_positive",
        ),
        CheckConstraint(
            "unit_cost >= 0",
            name="ck_stock_lot_consumption_unit_cost_nonnegative",
        ),
        Index(
            "ix_stock_lot_consumption_document",
            "company_id",
            "issue_document_id",
        ),
        Index(
            "ix_stock_lot_consumption_lot",
            "stock_lot_id",
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

    issue_document_id: Mapped[int] = mapped_column(
        ForeignKey(
            "documents.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
        index=True,
    )

    issue_document_line_id: Mapped[int] = mapped_column(
        ForeignKey(
            "document_lines.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
        
    )

    stock_lot_id: Mapped[int] = mapped_column(
        ForeignKey(
            "stock_lots.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )

    quantity: Mapped[Decimal] = mapped_column(
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