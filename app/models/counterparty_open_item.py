from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Enum as SQLEnum,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.services.counterparty_open_item_types import (
    CounterpartyOpenItemStatus,
    CounterpartyOpenItemType,
)


class CounterpartyOpenItem(Base):
    """
    Persistent AR/AP obligation created from a Trade Invoice.

    One confirmed Trade Invoice produces at most one open item.

    This table stores the immutable obligation amount. The remaining
    open amount will later be derived from persistent settlement
    allocations rather than maintained as a mutable balance column.
    """

    __tablename__ = "counterparty_open_items"

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "id",
            name=(
                "uq_counterparty_open_items_"
                "company_id_id"
            ),
        ),
        UniqueConstraint(
            "company_id",
            "trade_document_id",
            name=(
                "uq_counterparty_open_items_"
                "company_trade_document"
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
                "fk_counterparty_open_items_"
                "company_trade_document"
            ),
            ondelete="RESTRICT",
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
                "fk_counterparty_open_items_"
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
                "fk_counterparty_open_items_"
                "company_counterparty_contract"
            ),
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            (
                "item_type IN "
                "('receivable', 'payable')"
            ),
            name=(
                "ck_counterparty_open_item_type"
            ),
        ),
        CheckConstraint(
            (
                "status IN ("
                "'open', "
                "'partially_settled', "
                "'settled', "
                "'cancelled'"
                ")"
            ),
            name=(
                "ck_counterparty_open_item_status"
            ),
        ),
        CheckConstraint(
            "original_amount > 0",
            name=(
                "ck_counterparty_open_item_"
                "original_amount_positive"
            ),
        ),
        CheckConstraint(
            "char_length(currency_code) = 3",
            name=(
                "ck_counterparty_open_item_"
                "currency_code_length"
            ),
        ),
        CheckConstraint(
            "due_date >= document_date",
            name=(
                "ck_counterparty_open_item_"
                "due_date_not_before_document_date"
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

    item_type: Mapped[
        CounterpartyOpenItemType
    ] = mapped_column(
        SQLEnum(
            CounterpartyOpenItemType,
            name=(
                "counterparty_open_item_type_enum"
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
        nullable=False,
        index=True,
    )

    status: Mapped[
        CounterpartyOpenItemStatus
    ] = mapped_column(
        SQLEnum(
            CounterpartyOpenItemStatus,
            name=(
                "counterparty_open_item_status_enum"
            ),
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            values_callable=lambda enum_class: [
                item.value
                for item in enum_class
            ],
            length=30,
        ),
        default=(
            CounterpartyOpenItemStatus.OPEN
        ),
        nullable=False,
        index=True,
    )

    document_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
        index=True,
    )

    due_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
        index=True,
    )

    currency_code: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
        index=True,
    )

    original_amount: Mapped[
        Decimal
    ] = mapped_column(
        Numeric(18, 2),
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
