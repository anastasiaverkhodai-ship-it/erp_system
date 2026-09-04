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
)
from sqlalchemy.orm import (
    Mapped,
    mapped_column,
)

from app.core.database import Base


class TradeValueCorrectionEvent(Base):
    """
    Immutable quantity-neutral commercial value correction source.

    direction describes the original Trade Invoice:
        sale
        purchase

    product_id is part of the existing immutable
    TradeDocumentLine provenance identity.

    This row does not itself authorize any Ukrainian VAT adjustment.
    VAT/RK is a separate tax-accounting lifecycle.

    Historical corrections are append-only.
    """

    __tablename__ = (
        "trade_value_correction_events"
    )

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "id",
            name=(
                "uq_tvce_event_company_id_id"
            ),
        ),
        UniqueConstraint(
            "company_id",
            "id",
            "direction",
            "trade_document_id",
            "trade_document_line_id",
            "product_id",
            name=(
                "uq_tvce_event_company_id_id_source"
            ),
        ),
        UniqueConstraint(
            "reversal_of_id",
            name=(
                "uq_tvce_event_reversal_of"
            ),
        ),
        ForeignKeyConstraint(
            (
                "company_id",
                "trade_document_id",
                "trade_document_line_id",
                "product_id",
            ),
            (
                "trade_document_lines.company_id",
                "trade_document_lines.trade_document_id",
                "trade_document_lines.id",
                "trade_document_lines.product_id",
            ),
            name=(
                "fk_tvce_event_trade_document_line"
            ),
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            (
                "company_id",
                "reversal_of_id",
                "direction",
                "trade_document_id",
                "trade_document_line_id",
                "product_id",
            ),
            (
                "trade_value_correction_events.company_id",
                "trade_value_correction_events.id",
                "trade_value_correction_events.direction",
                (
                    "trade_value_correction_events."
                    "trade_document_id"
                ),
                (
                    "trade_value_correction_events."
                    "trade_document_line_id"
                ),
                (
                    "trade_value_correction_events."
                    "product_id"
                ),
            ),
            name=(
                "fk_tvce_event_reversal_source"
            ),
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "direction IN ('sale', 'purchase')",
            name=(
                "ck_tvce_event_direction"
            ),
        ),
        CheckConstraint(
            "original_gross_amount >= 0",
            name=(
                "ck_tvce_event_original_gross_nonnegative"
            ),
        ),
        CheckConstraint(
            "original_tax_amount >= 0",
            name=(
                "ck_tvce_event_original_tax_nonnegative"
            ),
        ),
        CheckConstraint(
            (
                "original_tax_amount "
                "<= original_gross_amount"
            ),
            name=(
                "ck_tvce_event_original_tax_not_above_gross"
            ),
        ),
        CheckConstraint(
            "corrected_gross_amount >= 0",
            name=(
                "ck_tvce_event_corrected_gross_nonnegative"
            ),
        ),
        CheckConstraint(
            "corrected_tax_amount >= 0",
            name=(
                "ck_tvce_event_corrected_tax_nonnegative"
            ),
        ),
        CheckConstraint(
            (
                "corrected_tax_amount "
                "<= corrected_gross_amount"
            ),
            name=(
                "ck_tvce_event_corrected_tax_not_above_gross"
            ),
        ),
        CheckConstraint(
            (
                "original_gross_amount "
                "<> corrected_gross_amount "
                "OR original_tax_amount "
                "<> corrected_tax_amount"
            ),
            name=(
                "ck_tvce_event_not_noop"
            ),
        ),
        CheckConstraint(
            "char_length(currency_code) = 3",
            name=(
                "ck_tvce_event_currency_length"
            ),
        ),
        CheckConstraint(
            (
                "reason_code IS NULL "
                "OR char_length(trim(reason_code)) > 0"
            ),
            name=(
                "ck_tvce_event_reason_nonempty"
            ),
        ),
        CheckConstraint(
            (
                "reversal_of_id IS NULL "
                "OR reversal_of_id <> id"
            ),
            name=(
                "ck_tvce_event_not_self_reversal"
            ),
        ),
        Index(
            "ix_tvce_event_trade_document_line",
            "company_id",
            "trade_document_id",
            "trade_document_line_id",
            "correction_date",
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

    trade_document_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    trade_document_line_id: Mapped[int] = mapped_column(
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

    correction_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )

    original_gross_amount: Mapped[Decimal] = mapped_column(
        Numeric(
            18,
            2,
        ),
        nullable=False,
    )

    original_tax_amount: Mapped[Decimal] = mapped_column(
        Numeric(
            18,
            2,
        ),
        nullable=False,
    )

    corrected_gross_amount: Mapped[Decimal] = mapped_column(
        Numeric(
            18,
            2,
        ),
        nullable=False,
    )

    corrected_tax_amount: Mapped[Decimal] = mapped_column(
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
