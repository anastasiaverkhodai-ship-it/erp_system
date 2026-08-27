from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    UniqueConstraint,
)
from sqlalchemy.orm import (
    Mapped,
    mapped_column,
    relationship,
)

from app.core.database import Base


if TYPE_CHECKING:
    from app.models.trade_fulfillment import (
        TradeFulfillment,
    )


class TradeFulfillmentLine(Base):
    """
    Persistent mapping:

        TradeDocumentLine
              ↓
        warehouse DocumentLine

    Both sides must describe the same product and warehouse.

    quantity is the fulfilled quantity represented by the
    target warehouse ISSUE line.
    """

    __tablename__ = "trade_fulfillment_lines"

    __table_args__ = (
        UniqueConstraint(
            "warehouse_document_line_id",
            name=(
                "uq_trade_fulfillment_lines_"
                "warehouse_document_line"
            ),
        ),
        ForeignKeyConstraint(
            [
                "company_id",
                "fulfillment_id",
                "trade_document_id",
                "warehouse_document_id",
            ],
            [
                "trade_fulfillments.company_id",
                "trade_fulfillments.id",
                "trade_fulfillments.trade_document_id",
                "trade_fulfillments.warehouse_document_id",
            ],
            name=(
                "fk_trade_fulfillment_lines_"
                "fulfillment"
            ),
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            [
                "company_id",
                "trade_document_id",
                "trade_document_line_id",
                "product_id",
                "warehouse_id",
            ],
            [
                "trade_document_lines.company_id",
                "trade_document_lines.trade_document_id",
                "trade_document_lines.id",
                "trade_document_lines.product_id",
                "trade_document_lines.warehouse_id",
            ],
            name=(
                "fk_trade_fulfillment_lines_"
                "trade_source"
            ),
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "warehouse_document_id",
                "warehouse_document_line_id",
                "product_id",
                "warehouse_id",
            ],
            [
                "document_lines.document_id",
                "document_lines.id",
                "document_lines.product_id",
                "document_lines.warehouse_id",
            ],
            name=(
                "fk_trade_fulfillment_lines_"
                "warehouse_target"
            ),
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "quantity > 0",
            name=(
                "ck_trade_fulfillment_line_"
                "quantity_positive"
            ),
        ),
        Index(
            "ix_trade_fulfillment_lines_source",
            "company_id",
            "trade_document_line_id",
            "id",
        ),
        Index(
            "ix_trade_fulfillment_lines_target_document",
            "warehouse_document_id",
            "id",
        ),
    )

    id: Mapped[int] = mapped_column(
        primary_key=True,
    )

    company_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    fulfillment_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    trade_document_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    trade_document_line_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    warehouse_document_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    warehouse_document_line_id: Mapped[int] = mapped_column(
        Integer,
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

    quantity: Mapped[Decimal] = mapped_column(
        Numeric(
            18,
            4,
        ),
        nullable=False,
    )

    fulfillment: Mapped[
        "TradeFulfillment"
    ] = relationship(
        "TradeFulfillment",
        back_populates="lines",
    )
