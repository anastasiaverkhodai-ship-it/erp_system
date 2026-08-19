from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Enum as SQLEnum,
    ForeignKey,
    Numeric,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.stock_ledger import StockMovementType


class MovingAverageMovement(Base):
    __tablename__ = "moving_average_movements"

    __table_args__ = (
        CheckConstraint(
    (
        "quantity_delta != 0 "
        "OR value_delta != 0"
    ),
    name=(
        "ck_moving_average_movement_"
        "nonzero_change"
    ),
),
        CheckConstraint(
            "unit_cost >= 0",
            name=(
                "ck_moving_average_movement_"
                "unit_cost_nonnegative"
            ),
        ),
        CheckConstraint(
            "balance_quantity_after >= 0",
            name=(
                "ck_moving_average_movement_"
                "balance_quantity_after_nonnegative"
            ),
        ),
        CheckConstraint(
            "balance_value_after >= 0",
            name=(
                "ck_moving_average_movement_"
                "balance_value_after_nonnegative"
            ),
        ),
        CheckConstraint(
            "average_unit_cost_after >= 0",
            name=(
                "ck_moving_average_movement_"
                "average_unit_cost_after_nonnegative"
            ),
        ),
        UniqueConstraint(
            "reversal_of_id",
            name=(
                "uq_moving_average_movement_"
                "reversal_of"
            ),
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
        index=True,
    )

    product_id: Mapped[int] = mapped_column(
        ForeignKey(
            "products.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
        index=True,
    )

    warehouse_id: Mapped[int] = mapped_column(
        ForeignKey(
            "warehouses.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
        index=True,
    )

    movement_type: Mapped[StockMovementType] = mapped_column(
        SQLEnum(
            StockMovementType,
            name="moving_average_movement_type_enum",
            native_enum=False,
            values_callable=lambda enum: [
                item.value
                for item in enum
            ],
        ),
        nullable=False,
    )

    movement_date: Mapped[date] = mapped_column(
    Date,
    nullable=False,
    index=True,
)

    quantity_delta: Mapped[Decimal] = mapped_column(
        Numeric(18, 4),
        nullable=False,
    )

    value_delta: Mapped[Decimal] = mapped_column(
        Numeric(20, 8),
        nullable=False,
    )

    unit_cost: Mapped[Decimal] = mapped_column(
        Numeric(20, 8),
        nullable=False,
    )

    balance_quantity_after: Mapped[Decimal] = mapped_column(
        Numeric(18, 4),
        nullable=False,
    )

    balance_value_after: Mapped[Decimal] = mapped_column(
        Numeric(20, 8),
        nullable=False,
    )

    average_unit_cost_after: Mapped[Decimal] = mapped_column(
        Numeric(20, 8),
        nullable=False,
    )

    reversal_of_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "moving_average_movements.id",
            ondelete="RESTRICT",
        ),
        nullable=True,
        index=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )