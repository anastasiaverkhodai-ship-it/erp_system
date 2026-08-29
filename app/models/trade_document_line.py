from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
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
    from app.models.trade_document import TradeDocument


class TradeDocumentLine(Base):
    __tablename__ = "trade_document_lines"

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "trade_document_id",
            "id",
            "product_id",
            name=(
                "uq_trade_document_lines_"
                "invoice_matching_source"
            ),
        ),
        UniqueConstraint(
            "trade_document_id",
            "line_number",
            name=(
                "uq_trade_document_line_"
                "document_line_number"
            ),
        ),
        UniqueConstraint(
            "company_id",
            "trade_document_id",
            "id",
            "product_id",
            "warehouse_id",
            name=(
                "uq_trade_document_lines_"
                "reservation_source"
            ),
        ),
        ForeignKeyConstraint(
            [
                "company_id",
                "trade_document_id",
            ],
            [
                "trade_documents.company_id",
                "trade_documents.id",
            ],
            name=(
                "fk_trade_document_lines_"
                "company_document"
            ),
            ondelete="CASCADE",
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
            name=(
                "fk_trade_document_lines_"
                "company_product"
            ),
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
            name=(
                "fk_trade_document_lines_"
                "company_warehouse"
            ),
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "line_number > 0",
            name=(
                "ck_trade_document_line_"
                "number_positive"
            ),
        ),
        CheckConstraint(
            "quantity > 0",
            name=(
                "ck_trade_document_line_"
                "quantity_positive"
            ),
        ),
        CheckConstraint(
            "unit_price >= 0",
            name=(
                "ck_trade_document_line_"
                "unit_price_nonnegative"
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

    trade_document_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    line_number: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    product_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    warehouse_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        index=True,
    )

    quantity: Mapped[Decimal] = mapped_column(
        Numeric(18, 4),
        nullable=False,
    )

    unit_price: Mapped[Decimal] = mapped_column(
        Numeric(18, 4),
        default=Decimal("0"),
        nullable=False,
    )

    trade_document: Mapped["TradeDocument"] = relationship(
        "TradeDocument",
        back_populates="lines",
    )
