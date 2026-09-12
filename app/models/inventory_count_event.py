from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class InventoryCountEvent(Base):
    """
    Immutable inventory observation.

    history_key identifies exactly one correction chain while
    allowing many independent inventory counts to remain ACTIVE.

        original
          -> reversal
          -> replacement
    """

    __tablename__ = "inventory_count_events"

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "id",
            name="uq_icev_company_id_id",
        ),
        UniqueConstraint(
            "company_id",
            "id",
            "history_key",
            name="uq_icev_history_identity",
        ),
        UniqueConstraint(
            "company_id",
            "id",
            "product_id",
            "warehouse_id",
            name="uq_icev_variance_parent_identity",
        ),
        UniqueConstraint(
            "reversal_of_id",
            name="uq_icev_reversal_of",
        ),
        ForeignKeyConstraint(
            [
                "company_id",
                "product_id",
            ],
            [
                "products.company_id",
                "products.id",
            ],
            name="fk_icev_company_product",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "company_id",
                "warehouse_id",
            ],
            [
                "warehouses.company_id",
                "warehouses.id",
            ],
            name="fk_icev_company_warehouse",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "company_id",
                "reversal_of_id",
                "history_key",
            ],
            [
                "inventory_count_events.company_id",
                "inventory_count_events.id",
                "inventory_count_events.history_key",
            ],
            name="fk_icev_history_reversal_of",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "expected_quantity >= 0",
            name="ck_icev_expected_quantity_nonnegative",
        ),
        CheckConstraint(
            "counted_quantity >= 0",
            name="ck_icev_counted_quantity_nonnegative",
        ),
        CheckConstraint(
            "reversal_of_id IS NULL OR reversal_of_id <> id",
            name="ck_icev_not_self_reversal",
        ),
        CheckConstraint(
            "length(history_key) = 36",
            name="ck_icev_history_key_length",
        ),
        Index(
            "ix_icev_stock_identity",
            "company_id",
            "product_id",
            "warehouse_id",
            "count_date",
            "id",
        ),
        Index(
            "ix_icev_history_chain",
            "company_id",
            "history_key",
            "id",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
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

    history_key: Mapped[str] = mapped_column(
        String(36),
        nullable=False,
        index=True,
    )

    product_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    warehouse_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    count_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
        index=True,
    )

    expected_quantity: Mapped[Decimal] = mapped_column(
        Numeric(18, 4),
        nullable=False,
    )

    counted_quantity: Mapped[Decimal] = mapped_column(
        Numeric(18, 4),
        nullable=False,
    )

    created_by: Mapped[int] = mapped_column(
        ForeignKey(
            "users.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )

    reversal_of_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        index=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )

    @property
    def variance_quantity(self) -> Decimal:
        return (
            Decimal(self.counted_quantity)
            - Decimal(self.expected_quantity)
        )
