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


class SalesReturnRecognitionEvent(Base):
    """
    Immutable economic Sales Return recognition event.

    Provenance is the pair:

        TradeReturnEvent
        +
        original SalesRecognitionEvent

    The physical return source and the original economic sale source
    therefore remain independently auditable.

    Original event accounting later becomes:

        Dr SALES_DEDUCTIONS
        Cr CUSTOMER_RECEIVABLES

    GENERAL 291:

        Dr 704
        Cr 361

    returned_gross_amount is the gross commercial amount allocated
    from the original SalesRecognitionEvent.

    returned_tax_amount is stored as immutable economic snapshot
    only. It does NOT authorize or post VAT adjustment. Ukrainian
    VAT/RK recognition remains a separate tax-accounting lifecycle.

    Historical rows are never updated or deleted. Reconciliation
    changes are represented through immutable reversal rows.
    """

    __tablename__ = (
        "sales_return_recognition_events"
    )

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "id",
            name=(
                "uq_srre_event_company_id_id"
            ),
        ),
        UniqueConstraint(
            "company_id",
            "id",
            "trade_return_event_id",
            "sales_recognition_event_id",
            name=(
                "uq_srre_event_company_id_id_source"
            ),
        ),
        UniqueConstraint(
            "reversal_of_id",
            name=(
                "uq_srre_event_reversal_of"
            ),
        ),
        ForeignKeyConstraint(
            (
                "company_id",
                "trade_return_event_id",
            ),
            (
                "trade_return_events.company_id",
                "trade_return_events.id",
            ),
            name=(
                "fk_srre_event_company_trade_return"
            ),
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            (
                "company_id",
                "sales_recognition_event_id",
            ),
            (
                "sales_recognition_events.company_id",
                "sales_recognition_events.id",
            ),
            name=(
                "fk_srre_event_company_sales_recognition"
            ),
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            (
                "company_id",
                "reversal_of_id",
                "trade_return_event_id",
                "sales_recognition_event_id",
            ),
            (
                "sales_return_recognition_events.company_id",
                "sales_return_recognition_events.id",
                (
                    "sales_return_recognition_events."
                    "trade_return_event_id"
                ),
                (
                    "sales_return_recognition_events."
                    "sales_recognition_event_id"
                ),
            ),
            name=(
                "fk_srre_event_reversal_source"
            ),
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "returned_quantity > 0",
            name=(
                "ck_srre_event_returned_quantity_positive"
            ),
        ),
        CheckConstraint(
            "returned_gross_amount > 0",
            name=(
                "ck_srre_event_returned_gross_positive"
            ),
        ),
        CheckConstraint(
            "returned_tax_amount >= 0",
            name=(
                "ck_srre_event_returned_tax_nonnegative"
            ),
        ),
        CheckConstraint(
            (
                "returned_tax_amount "
                "<= returned_gross_amount"
            ),
            name=(
                "ck_srre_event_tax_not_above_gross"
            ),
        ),
        CheckConstraint(
            "char_length(currency_code) = 3",
            name=(
                "ck_srre_event_currency_length"
            ),
        ),
        CheckConstraint(
            (
                "reversal_of_id IS NULL "
                "OR reversal_of_id <> id"
            ),
            name=(
                "ck_srre_event_not_self_reversal"
            ),
        ),
        Index(
            "ix_srre_event_pair_history",
            "company_id",
            "trade_return_event_id",
            "sales_recognition_event_id",
            "id",
            unique=False,
        ),
        Index(
            "ix_srre_event_trade_return",
            "company_id",
            "trade_return_event_id",
            "recognition_date",
            "id",
        ),
        Index(
            "ix_srre_event_sales_recognition",
            "company_id",
            "sales_recognition_event_id",
            "recognition_date",
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

    trade_return_event_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    sales_recognition_event_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    recognition_date: Mapped[date] = mapped_column(
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

    returned_gross_amount: Mapped[Decimal] = mapped_column(
        Numeric(
            18,
            2,
        ),
        nullable=False,
    )

    returned_tax_amount: Mapped[Decimal] = mapped_column(
        Numeric(
            18,
            2,
        ),
        nullable=False,
    )

    currency_code: Mapped[str] = mapped_column(
        String(3),
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
