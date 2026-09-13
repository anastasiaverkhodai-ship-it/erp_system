from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class BankStatementLine(Base):
    """
    Immutable external bank transaction evidence.

    Economic/imported fields are append-only.
    Corrections must create new evidence or be handled by
    later reconciliation/reversal layers.
    """

    __tablename__ = "bank_statement_lines"

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "id",
            name="uq_bank_statement_lines_company_id_id",
        ),
        UniqueConstraint(
            "company_id",
            "bank_statement_id",
            "external_line_id",
            name=(
                "uq_bank_statement_lines_"
                "statement_external_line"
            ),
        ),
        ForeignKeyConstraint(
            [
                "company_id",
                "bank_statement_id",
                "bank_account_id",
            ],
            [
                "bank_statements.company_id",
                "bank_statements.id",
                "bank_statements.bank_account_id",
            ],
            name="fk_bank_statement_lines_statement_identity",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "length(trim(external_line_id)) > 0",
            name=(
                "ck_bank_statement_lines_"
                "external_line_id_nonempty"
            ),
        ),
        CheckConstraint(
            "amount <> 0",
            name="ck_bank_statement_lines_amount_nonzero",
        ),
        CheckConstraint(
            "char_length(currency_code) = 3",
            name=(
                "ck_bank_statement_lines_"
                "currency_code_length"
            ),
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    company_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    bank_statement_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    bank_account_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    external_line_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    transaction_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
        index=True,
    )

    value_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )

    amount: Mapped[Decimal] = mapped_column(
        Numeric(
            precision=18,
            scale=2,
        ),
        nullable=False,
    )

    currency_code: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
        index=True,
    )

    counterparty_name: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )

    counterparty_account: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    payment_reference: Mapped[str | None] = mapped_column(
        String(1000),
        nullable=True,
    )

    description: Mapped[str | None] = mapped_column(
        String(2000),
        nullable=True,
    )

    raw_payload: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
