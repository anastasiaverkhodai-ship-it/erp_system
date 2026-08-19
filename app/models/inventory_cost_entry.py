from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum as SQLEnum,
    ForeignKey,
    Numeric,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.company import InventoryValuationMethod


class InventoryCostEntry(Base):
    __tablename__ = "inventory_cost_entries"

    __table_args__ = (
        UniqueConstraint(
            "document_line_id",
            name="uq_inventory_cost_entry_document_line",
        ),
        CheckConstraint(
            "quantity > 0",
            name="ck_inventory_cost_entry_quantity_positive",
        ),
        CheckConstraint(
            "unit_cost >= 0",
            name="ck_inventory_cost_entry_unit_cost_nonnegative",
        ),
        CheckConstraint(
            "cost_amount >= 0",
            name="ck_inventory_cost_entry_amount_nonnegative",
        ),

        CheckConstraint(
    "valuation_amount >= 0",
    name="ck_inventory_cost_entry_valuation_amount_nonnegative",
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

    document_id: Mapped[int] = mapped_column(
        ForeignKey(
            "documents.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
        index=True,
    )

    document_line_id: Mapped[int] = mapped_column(
        ForeignKey(
            "document_lines.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )

    valuation_method: Mapped[
        InventoryValuationMethod
    ] = mapped_column(
        SQLEnum(
            InventoryValuationMethod,
            name="inventory_valuation_method_enum",
            native_enum=False,
            values_callable=lambda enum: [
                item.value for item in enum
            ],
        ),
        nullable=False,
    )

    quantity: Mapped[Decimal] = mapped_column(
        Numeric(18, 4),
        nullable=False,
    )

    unit_cost: Mapped[Decimal] = mapped_column(
        Numeric(20, 8),
        nullable=False,
    )

    valuation_amount: Mapped[Decimal] = mapped_column(
    Numeric(20, 8),
    nullable=False,
)
    cost_amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )