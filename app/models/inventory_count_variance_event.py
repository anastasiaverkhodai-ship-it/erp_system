from datetime import date, datetime
from decimal import Decimal
from enum import Enum

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Enum as SQLEnum,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.document import DocumentType


class InventoryCountVarianceDirection(str, Enum):
    INCREASE = "increase"
    DECREASE = "decrease"


class InventoryCountVarianceEvent(Base):
    """
    Immutable non-zero stock correction derived from a count.

    The linked warehouse document must be ADJUSTMENT and must carry
    the exact same company/product/warehouse stock identity.

    Valuation remains a separate immutable layer.
    """

    __tablename__ = "inventory_count_variance_events"

    __table_args__ = (
        ForeignKeyConstraint(
            [
                "company_id",
                "inventory_count_event_id",
                "product_id",
                "warehouse_id",
            ],
            [
                "inventory_count_events.company_id",
                "inventory_count_events.id",
                "inventory_count_events.product_id",
                "inventory_count_events.warehouse_id",
            ],
            name="fk_icve_count_stock_identity",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "company_id",
                "document_id",
                "document_type",
            ],
            [
                "documents.company_id",
                "documents.id",
                "documents.document_type",
            ],
            name="fk_icve_company_adjustment_document",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "document_id",
                "document_line_id",
                "product_id",
                "warehouse_id",
            ],
            [
                "document_lines.document_id",
                "document_lines.id",
                "document_lines.product_id",
                "document_lines.warehouse_id",
            ],
            name="fk_icve_adjustment_document_line",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "inventory_count_event_id",
            name="uq_icve_inventory_count_event",
        ),
        UniqueConstraint(
            "document_line_id",
            name="uq_icve_document_line",
        ),
        CheckConstraint(
            "quantity > 0",
            name="ck_icve_quantity_positive",
        ),
        CheckConstraint(
            "direction IN ('increase', 'decrease')",
            name="ck_icve_direction",
        ),
        CheckConstraint(
            "document_type = 'adjustment'",
            name="ck_icve_document_type_adjustment",
        ),
        Index(
            "ix_icve_stock_identity",
            "company_id",
            "product_id",
            "warehouse_id",
            "adjustment_date",
            "id",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    company_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    inventory_count_event_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    document_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    document_line_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    document_type: Mapped[DocumentType] = mapped_column(
        SQLEnum(
            DocumentType,
            name="document_type_enum",
            native_enum=False,
            values_callable=lambda enum: [
                item.value
                for item in enum
            ],
        ),
        nullable=False,
        default=DocumentType.ADJUSTMENT,
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

    adjustment_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
        index=True,
    )

    direction: Mapped[
        InventoryCountVarianceDirection
    ] = mapped_column(
        SQLEnum(
            InventoryCountVarianceDirection,
            name="inventory_count_variance_direction_enum",
            native_enum=False,
            values_callable=lambda enum: [
                item.value
                for item in enum
            ],
        ),
        nullable=False,
    )

    quantity: Mapped[Decimal] = mapped_column(
        Numeric(18, 4),
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )

    @property
    def signed_quantity(self) -> Decimal:
        quantity = Decimal(self.quantity)

        if (
            self.direction
            == InventoryCountVarianceDirection.INCREASE
        ):
            return quantity

        return -quantity
