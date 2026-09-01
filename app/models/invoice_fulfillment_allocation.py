from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum as SQLEnum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.services.invoice_fulfillment_allocation_types import (
    InvoiceFulfillmentAllocationStatus,
)


class InvoiceFulfillmentAllocation(Base):
    """
    Persistent line-level matching between a confirmed Trade Invoice
    and one persistent Trade Fulfillment line.

    The record stores quantity only.

    Invoice commercial value is derived from the immutable confirmed
    invoice line. Commercial Sales recognition is persisted
    separately through immutable SalesRecognitionEvent rows and must
    never be derived from the warehouse DocumentLine price.

    Reversal never deletes the original allocation.
    """

    __tablename__ = "invoice_fulfillment_allocations"

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "id",
            name=(
                "uq_invoice_fulfillment_allocations_"
                "company_id_id"
            ),
        ),
        ForeignKeyConstraint(
            [
                "company_id",
                "invoice_id",
                "invoice_line_id",
                "product_id",
            ],
            [
                "trade_document_lines.company_id",
                "trade_document_lines.trade_document_id",
                "trade_document_lines.id",
                "trade_document_lines.product_id",
            ],
            name=(
                "fk_invoice_fulfillment_allocations_"
                "invoice_line"
            ),
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "company_id",
                "fulfillment_id",
                "order_id",
                "order_line_id",
                "fulfillment_line_id",
                "product_id",
            ],
            [
                "trade_fulfillment_lines.company_id",
                "trade_fulfillment_lines.fulfillment_id",
                "trade_fulfillment_lines.trade_document_id",
                "trade_fulfillment_lines.trade_document_line_id",
                "trade_fulfillment_lines.id",
                "trade_fulfillment_lines.product_id",
            ],
            name=(
                "fk_invoice_fulfillment_allocations_"
                "fulfillment_line"
            ),
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "quantity > 0",
            name=(
                "ck_invoice_fulfillment_allocations_"
                "quantity_positive"
            ),
        ),
        CheckConstraint(
            "invoice_id <> order_id",
            name=(
                "ck_invoice_fulfillment_allocations_"
                "different_documents"
            ),
        ),
        CheckConstraint(
            "status IN ('active', 'reversed')",
            name=(
                "ck_invoice_fulfillment_allocations_"
                "status"
            ),
        ),
        CheckConstraint(
            "("
            "status = 'active' "
            "AND reversed_by IS NULL "
            "AND reversed_at IS NULL"
            ") OR ("
            "status = 'reversed' "
            "AND reversed_by IS NOT NULL "
            "AND reversed_at IS NOT NULL"
            ")",
            name=(
                "ck_invoice_fulfillment_allocations_"
                "reversal_state"
            ),
        ),
        Index(
            "ix_if_alloc_invoice_active",
            "company_id",
            "invoice_line_id",
            "id",
            postgresql_where=text(
                "status = 'active'"
            ),
        ),
        Index(
            "ix_if_alloc_fulfillment_active",
            "company_id",
            "fulfillment_line_id",
            "id",
            postgresql_where=text(
                "status = 'active'"
            ),
        ),
        Index(
            "uq_if_alloc_active_pair",
            "company_id",
            "invoice_line_id",
            "fulfillment_line_id",
            unique=True,
            postgresql_where=text(
                "status = 'active'"
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

    invoice_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    invoice_line_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    fulfillment_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    fulfillment_line_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    order_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    order_line_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    product_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    quantity: Mapped[Decimal] = mapped_column(
        Numeric(
            18,
            4,
        ),
        nullable=False,
    )

    status: Mapped[
        InvoiceFulfillmentAllocationStatus
    ] = mapped_column(
        SQLEnum(
            InvoiceFulfillmentAllocationStatus,
            name=(
                "invoice_fulfillment_"
                "allocation_status_enum"
            ),
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            values_callable=lambda enum_class: [
                item.value
                for item in enum_class
            ],
            length=20,
        ),
        default=(
            InvoiceFulfillmentAllocationStatus.ACTIVE
        ),
        nullable=False,
        index=True,
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
            timezone=True,
        ),
        nullable=False,
        server_default=func.now(),
    )

    reversed_by: Mapped[int | None] = mapped_column(
        ForeignKey(
            "users.id",
            ondelete="RESTRICT",
        ),
        nullable=True,
    )

    reversed_at: Mapped[
        datetime | None
    ] = mapped_column(
        DateTime(
            timezone=True,
        ),
        nullable=True,
    )
