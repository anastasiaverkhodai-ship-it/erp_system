from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Enum as SQLEnum,
    ForeignKey,
    ForeignKeyConstraint,
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
from app.services.trade_document_types import (
    TradeDirection,
    TradeDocumentKind,
    TradeDocumentStatus,
)


class TradeDocument(Base):
    __tablename__ = "trade_documents"

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "id",
            name="uq_trade_documents_company_id_id",
        ),
        UniqueConstraint(
            "company_id",
            "direction",
            "kind",
            "number",
            name=(
                "uq_trade_document_"
                "company_direction_kind_number"
            ),
        ),
        ForeignKeyConstraint(
            [
                "company_id",
                "counterparty_id",
            ],
            [
                "counterparties.company_id",
                "counterparties.id",
            ],
            name=(
                "fk_trade_documents_"
                "company_counterparty"
            ),
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "company_id",
                "counterparty_id",
                "contract_id",
            ],
            [
                "contracts.company_id",
                "contracts.counterparty_id",
                "contracts.id",
            ],
            name=(
                "fk_trade_documents_"
                "company_counterparty_contract"
            ),
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "direction IN ('sale', 'purchase')",
            name="ck_trade_document_direction",
        ),
        CheckConstraint(
            "kind IN ('order', 'invoice')",
            name="ck_trade_document_kind",
        ),
        CheckConstraint(
            (
                "status IN ("
                "'draft', "
                "'confirmed', "
                "'partially_fulfilled', "
                "'fulfilled', "
                "'cancelled'"
                ")"
            ),
            name="ck_trade_document_status",
        ),
        CheckConstraint(
            "char_length(currency_code) = 3",
            name="ck_trade_document_currency_code_length",
        ),
        CheckConstraint(
            "payment_term_days >= 0",
            name=(
                "ck_trade_document_"
                "payment_term_days_nonnegative"
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

    counterparty_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    contract_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        index=True,
    )

    number: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    direction: Mapped[TradeDirection] = mapped_column(
        SQLEnum(
            TradeDirection,
            name="trade_direction_enum",
            native_enum=False,
            create_constraint=False,
            values_callable=lambda enum: [
                item.value
                for item in enum
            ],
        ),
        nullable=False,
    )

    kind: Mapped[TradeDocumentKind] = mapped_column(
        SQLEnum(
            TradeDocumentKind,
            name="trade_document_kind_enum",
            native_enum=False,
            create_constraint=False,
            values_callable=lambda enum: [
                item.value
                for item in enum
            ],
        ),
        nullable=False,
    )

    status: Mapped[TradeDocumentStatus] = mapped_column(
        SQLEnum(
            TradeDocumentStatus,
            name="trade_document_status_enum",
            native_enum=False,
            create_constraint=False,
            values_callable=lambda enum: [
                item.value
                for item in enum
            ],
        ),
        default=TradeDocumentStatus.DRAFT,
        nullable=False,
    )

    document_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )

    currency_code: Mapped[str] = mapped_column(
        String(3),
        default="UAH",
        nullable=False,
    )

    payment_term_days: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )

    created_by: Mapped[int] = mapped_column(
        ForeignKey(
            "users.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    cancelled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    lines: Mapped[list["TradeDocumentLine"]] = relationship(
        "TradeDocumentLine",
        back_populates="trade_document",
        cascade="all, delete-orphan",
    )
