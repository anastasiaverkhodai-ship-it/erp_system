from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Numeric,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class MovingAverageBalance(Base):
    __tablename__ = "moving_average_balances"

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "product_id",
            "warehouse_id",
            name=(
                "uq_moving_average_balance_"
                "company_product_warehouse"
            ),
        ),
        CheckConstraint(
            "quantity >= 0",
            name=(
                "ck_moving_average_balance_"
                "quantity_nonnegative"
            ),
        ),
        CheckConstraint(
            "inventory_value >= 0",
            name=(
                "ck_moving_average_balance_"
                "inventory_value_nonnegative"
            ),
        ),
        CheckConstraint(
            "average_unit_cost >= 0",
            name=(
                "ck_moving_average_balance_"
                "average_unit_cost_nonnegative"
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

    quantity: Mapped[Decimal] = mapped_column(
        Numeric(18, 4),
        default=Decimal("0"),
        nullable=False,
    )

    inventory_value: Mapped[Decimal] = mapped_column(
    Numeric(20, 8),
    default=Decimal("0"),
    nullable=False,
)

    average_unit_cost: Mapped[Decimal] = mapped_column(
        Numeric(20, 8),
        default=Decimal("0"),
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )