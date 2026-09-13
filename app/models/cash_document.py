from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
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
from app.services.payment_types import PaymentDirection


class CashDocument(Base):
    """
    Immutable operational cash evidence.

    CashDocument is not Payment and does not post GL directly.

    Original:
        reversal_of_id = NULL

    Operational reversal:
        reversal_of_id = original CashDocument.id

    Historical rows are never updated or deleted by lifecycle services.
    """

    __tablename__ = "cash_documents"

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "id",
            name="uq_cash_documents_company_id_id",
        ),
        UniqueConstraint(
            "company_id",
            "direction",
            "document_number",
            name="uq_cash_documents_company_direction_number",
        ),
        UniqueConstraint(
            "company_id",
            "id",
            "cash_desk_id",
            "payment_id",
            name="uq_cash_documents_identity",
        ),
        UniqueConstraint(
            "reversal_of_id",
            name="uq_cash_documents_reversal_of_id",
        ),
        ForeignKeyConstraint(
            ["company_id", "cash_desk_id"],
            ["cash_desks.company_id", "cash_desks.id"],
            name="fk_cash_documents_company_cash_desk",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "payment_id"],
            ["payments.company_id", "payments.id"],
            name="fk_cash_documents_company_payment",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "company_id",
                "reversal_of_id",
                "cash_desk_id",
                "payment_id",
            ],
            [
                "cash_documents.company_id",
                "cash_documents.id",
                "cash_documents.cash_desk_id",
                "cash_documents.payment_id",
            ],
            name="fk_cash_documents_reversal_identity",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "counterparty_id"],
            ["counterparties.company_id", "counterparties.id"],
            name="fk_cash_documents_company_counterparty",
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
            name="fk_cash_documents_company_counterparty_contract",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "direction IN ('incoming', 'outgoing')",
            name="ck_cash_documents_direction",
        ),
        CheckConstraint(
            "amount > 0",
            name="ck_cash_documents_amount_positive",
        ),
        CheckConstraint(
            "char_length(currency_code) = 3",
            name="ck_cash_documents_currency_length",
        ),
        CheckConstraint(
            "length(trim(document_number)) > 0",
            name="ck_cash_documents_number_nonempty",
        ),
        CheckConstraint(
            "reversal_of_id IS NULL OR reversal_of_id <> id",
            name="ck_cash_documents_not_self_reversal",
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
        index=True,
    )

    cash_desk_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    payment_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    document_number: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    direction: Mapped[PaymentDirection] = mapped_column(
        String(20),
        nullable=False,
        index=True,
    )

    document_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
        index=True,
    )

    amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
    )

    currency_code: Mapped[str] = mapped_column(
        String(3),
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

    external_reference: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        index=True,
    )

    description: Mapped[str | None] = mapped_column(
        String(500),
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
        server_default=func.now(),
        nullable=False,
    )

    reversal_of_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        index=True,
    )
