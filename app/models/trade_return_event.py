from datetime import (
    date,
    datetime,
)
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
    func,
    text,
)
from sqlalchemy.orm import (
    Mapped,
    mapped_column,
)

from app.core.database import Base


class TradeReturnEvent(Base):
    """
    Immutable physical Trade return source.

    direction describes the ORIGINAL trade:

        sale
            customer returns goods to us;
            return warehouse document must be RECEIPT.

        purchase
            goods are returned to supplier;
            return warehouse document must be ISSUE.

    Full source provenance is persisted because the existing ERP
    source tables intentionally use composite identities.

    This event does not itself:
    - post accounting;
    - modify AR/AP;
    - restore or reverse COGS;
    - recognize VAT;
    - create an Ukrainian VAT adjustment / RK.

    History is append-only. A reversal is another immutable row.
    """

    __tablename__ = "trade_return_events"

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "id",
            name="uq_tre_event_company_id_id",
        ),
        UniqueConstraint(
            "company_id",
            "id",
            "direction",
            "original_fulfillment_id",
            "original_trade_document_id",
            "original_trade_document_line_id",
            "original_fulfillment_line_id",
            "product_id",
            "return_document_id",
            "return_document_type",
            "return_document_line_id",
            "return_warehouse_id",
            name=(
                "uq_tre_event_company_id_id_source"
            ),
        ),
        UniqueConstraint(
            "reversal_of_id",
            name="uq_tre_event_reversal_of",
        ),
        ForeignKeyConstraint(
            (
                "company_id",
                "original_trade_document_id",
                "original_trade_document_line_id",
                "product_id",
            ),
            (
                "trade_document_lines.company_id",
                "trade_document_lines.trade_document_id",
                "trade_document_lines.id",
                "trade_document_lines.product_id",
            ),
            name=(
                "fk_tre_event_original_trade_line"
            ),
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            (
                "company_id",
                "original_fulfillment_id",
                "original_trade_document_id",
                "original_trade_document_line_id",
                "original_fulfillment_line_id",
                "product_id",
            ),
            (
                "trade_fulfillment_lines.company_id",
                "trade_fulfillment_lines.fulfillment_id",
                "trade_fulfillment_lines.trade_document_id",
                "trade_fulfillment_lines.trade_document_line_id",
                "trade_fulfillment_lines.id",
                "trade_fulfillment_lines.product_id",
            ),
            name=(
                "fk_tre_event_original_fulfillment_line"
            ),
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            (
                "company_id",
                "return_document_id",
                "return_document_type",
            ),
            (
                "documents.company_id",
                "documents.id",
                "documents.document_type",
            ),
            name="fk_tre_event_return_document",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            (
                "return_document_id",
                "return_document_line_id",
                "product_id",
                "return_warehouse_id",
            ),
            (
                "document_lines.document_id",
                "document_lines.id",
                "document_lines.product_id",
                "document_lines.warehouse_id",
            ),
            name="fk_tre_event_return_document_line",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            (
                "company_id",
                "reversal_of_id",
                "direction",
                "original_fulfillment_id",
                "original_trade_document_id",
                "original_trade_document_line_id",
                "original_fulfillment_line_id",
                "product_id",
                "return_document_id",
                "return_document_type",
                "return_document_line_id",
                "return_warehouse_id",
            ),
            (
                "trade_return_events.company_id",
                "trade_return_events.id",
                "trade_return_events.direction",
                "trade_return_events.original_fulfillment_id",
                (
                    "trade_return_events."
                    "original_trade_document_id"
                ),
                (
                    "trade_return_events."
                    "original_trade_document_line_id"
                ),
                (
                    "trade_return_events."
                    "original_fulfillment_line_id"
                ),
                "trade_return_events.product_id",
                "trade_return_events.return_document_id",
                "trade_return_events.return_document_type",
                "trade_return_events.return_document_line_id",
                "trade_return_events.return_warehouse_id",
            ),
            name="fk_tre_event_reversal_source",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "direction IN ('sale', 'purchase')",
            name="ck_tre_event_direction",
        ),
        CheckConstraint(
            (
                "return_document_type "
                "IN ('receipt', 'issue')"
            ),
            name=(
                "ck_tre_event_return_document_type"
            ),
        ),
        CheckConstraint(
            (
                "("
                "direction = 'sale' "
                "AND return_document_type = 'receipt'"
                ") OR ("
                "direction = 'purchase' "
                "AND return_document_type = 'issue'"
                ")"
            ),
            name=(
                "ck_tre_event_direction_document_type"
            ),
        ),
        CheckConstraint(
            "returned_quantity > 0",
            name=(
                "ck_tre_event_returned_quantity_positive"
            ),
        ),
        CheckConstraint(
            (
                "reason_code IS NULL "
                "OR char_length(trim(reason_code)) > 0"
            ),
            name="ck_tre_event_reason_nonempty",
        ),
        CheckConstraint(
            (
                "reversal_of_id IS NULL "
                "OR reversal_of_id <> id"
            ),
            name="ck_tre_event_not_self_reversal",
        ),
        Index(
            "uq_tre_event_original_return_line",
            "company_id",
            "return_document_id",
            "return_document_line_id",
            unique=True,
            postgresql_where=text(
                "reversal_of_id IS NULL"
            ),
        ),
        Index(
            "ix_tre_event_original_fulfillment_source",
            "company_id",
            "original_fulfillment_id",
            "original_fulfillment_line_id",
            "return_date",
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
    )

    direction: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
    )

    original_fulfillment_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    original_trade_document_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    original_trade_document_line_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    original_fulfillment_line_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    product_id: Mapped[int] = mapped_column(
        ForeignKey(
            "products.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )

    return_document_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    return_document_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )

    return_document_line_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    return_warehouse_id: Mapped[int] = mapped_column(
        ForeignKey(
            "warehouses.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )

    return_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )

    returned_quantity: Mapped[Decimal] = mapped_column(
        Numeric(
            18,
            4,
        ),
        nullable=False,
    )

    reason_code: Mapped[str | None] = mapped_column(
        String(40),
        nullable=True,
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
            timezone=True
        ),
        server_default=func.now(),
        nullable=False,
    )

    reversal_of_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
