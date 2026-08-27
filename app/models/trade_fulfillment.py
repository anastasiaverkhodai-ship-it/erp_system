from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import (
    Mapped,
    mapped_column,
    relationship,
)

from app.core.database import Base


if TYPE_CHECKING:
    from app.models.trade_fulfillment_line import (
        TradeFulfillmentLine,
    )


class TradeFulfillment(Base):
    """
    Persistent link between a TradeDocument Sales Order
    and one warehouse ISSUE document.

    One Sales Order may have many fulfillment records because
    it may be shipped partially through multiple ISSUE documents.

    One warehouse ISSUE document may belong to only one
    trade fulfillment.
    """

    __tablename__ = "trade_fulfillments"

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "id",
            "trade_document_id",
            "warehouse_document_id",
            name=(
                "uq_trade_fulfillments_"
                "line_source"
            ),
        ),
        UniqueConstraint(
            "company_id",
            "warehouse_document_id",
            name=(
                "uq_trade_fulfillments_"
                "company_warehouse_document"
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
                "fk_trade_fulfillments_"
                "company_trade_document"
            ),
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "company_id",
                "warehouse_document_id",
                "warehouse_document_type",
            ],
            [
                "documents.company_id",
                "documents.id",
                "documents.document_type",
            ],
            name=(
                "fk_trade_fulfillments_"
                "company_warehouse_document"
            ),
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "warehouse_document_type = 'issue'",
            name=(
                "ck_trade_fulfillment_"
                "warehouse_document_issue"
            ),
        ),
        Index(
            "ix_trade_fulfillments_source",
            "company_id",
            "trade_document_id",
            "id",
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

    warehouse_document_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    warehouse_document_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="issue",
        server_default="issue",
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
        nullable=False,
        server_default=func.now(),
    )

    lines: Mapped[
        list["TradeFulfillmentLine"]
    ] = relationship(
        "TradeFulfillmentLine",
        back_populates="fulfillment",
        cascade="all, delete-orphan",
    )
