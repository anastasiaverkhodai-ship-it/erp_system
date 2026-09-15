from datetime import date, datetime
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
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class PurchaseLandedCostEvent(Base):
    """
    Immutable purchase landed-cost economic source.

    Represents an additional acquisition cost attributable to one
    posted PURCHASE receipt provenance.

    The event is quantity-neutral. It does not alter received quantity
    and does not itself authorize Ukrainian VAT/RK treatment.

    Historical corrections are append-only. Reversal is represented
    by another event referencing reversal_of_id.
    """

    __tablename__ = "purchase_landed_cost_events"

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "id",
            name="uq_plce_company_id_id",
        ),
        UniqueConstraint(
            "reversal_of_id",
            name="uq_plce_reversal_of",
        ),
        ForeignKeyConstraint(
            (
                "company_id",
                "trade_document_id",
            ),
            (
                "trade_documents.company_id",
                "trade_documents.id",
            ),
            name="fk_plce_trade_document",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ("warehouse_document_id",),
            ("documents.id",),
            name="fk_plce_warehouse_document",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            (
                "company_id",
                "reversal_of_id",
            ),
            (
                "purchase_landed_cost_events.company_id",
                "purchase_landed_cost_events.id",
            ),
            name="fk_plce_reversal_source",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "amount > 0",
            name="ck_plce_amount_positive",
        ),
        CheckConstraint(
            "char_length(currency_code) = 3",
            name="ck_plce_currency_length",
        ),
        CheckConstraint(
            (
                "reason_code IS NULL "
                "OR char_length(trim(reason_code)) > 0"
            ),
            name="ck_plce_reason_nonempty",
        ),
        CheckConstraint(
            (
                "reversal_of_id IS NULL "
                "OR reversal_of_id <> id"
            ),
            name="ck_plce_not_self_reversal",
        ),
        Index(
            "ix_plce_company",
            "company_id",
        ),
        Index(
            "ix_plce_trade_doc",
            "trade_document_id",
        ),
        Index(
            "ix_plce_wh_doc",
            "warehouse_document_id",
        ),
        Index(
            "ix_plce_cost_date",
            "cost_date",
        ),
        Index(
            "ix_plce_reversal",
            "reversal_of_id",
        ),
        Index(
            "ix_plce_receipt",
            "company_id",
            "warehouse_document_id",
            "cost_date",
            "id",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    company_id: Mapped[int] = mapped_column(
        ForeignKey(
            "companies.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )

    trade_document_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    warehouse_document_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
    )

    currency_code: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
    )

    cost_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )

    reason_code: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )

    reversal_of_id: Mapped[int | None] = mapped_column(
        Integer,
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
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
