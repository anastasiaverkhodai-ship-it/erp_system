from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    Index,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class BankStatementReconciliation(Base):
    """
    Immutable bank reconciliation event ledger.

    Original match:
        reversal_of_id = NULL

    Unmatch/reversal:
        reversal_of_id = original reconciliation id

    Historical rows are never mutated or deleted by lifecycle
    services. Current active state is maintained separately by
    BankStatementReconciliationActiveLink.
    """

    __tablename__ = "bank_statement_reconciliations"

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "id",
            name=(
                "uq_bank_statement_reconciliations_"
                "company_id_id"
            ),
        ),
        UniqueConstraint(
            "reversal_of_id",
            name=(
                "uq_bank_statement_reconciliations_"
                "reversal_of_id"
            ),
        ),
        ForeignKeyConstraint(
            [
                "company_id",
                "bank_statement_line_id",
            ],
            [
                "bank_statement_lines.company_id",
                "bank_statement_lines.id",
            ],
            name=(
                "fk_bank_statement_reconciliations_"
                "statement_line"
            ),
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "company_id",
                "payment_id",
            ],
            [
                "payments.company_id",
                "payments.id",
            ],
            name=(
                "fk_bank_statement_reconciliations_"
                "payment"
            ),
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "company_id",
                "reversal_of_id",
            ],
            [
                "bank_statement_reconciliations.company_id",
                "bank_statement_reconciliations.id",
            ],
            name=(
                "fk_bank_statement_reconciliations_"
                "reversal_of"
            ),
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "matched_amount > 0",
            name=(
                "ck_bank_statement_reconciliations_"
                "amount_positive"
            ),
        ),
        CheckConstraint(
            "char_length(currency_code) = 3",
            name=(
                "ck_bank_statement_reconciliations_"
                "currency_length"
            ),
        ),
        CheckConstraint(
            "reversal_of_id IS NULL OR reversal_of_id <> id",
            name=(
                "ck_bank_statement_reconciliations_"
                "not_self_reversal"
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

    bank_statement_line_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    payment_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    matched_amount: Mapped[Decimal] = mapped_column(
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


class BankStatementReconciliationActiveLink(Base):
    """
    Mutable current-state projection.

    This table is not audit history. Immutable reconciliation
    history lives exclusively in bank_statement_reconciliations.
    """

    __tablename__ = "bank_statement_reconciliation_active_links"

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "bank_statement_line_id",
            name=(
                "uq_bank_reconciliation_active_link_line"
            ),
        ),
        UniqueConstraint(
            "company_id",
            "reconciliation_id",
            name=(
                "uq_bank_reconciliation_active_link_event"
            ),
        ),
        ForeignKeyConstraint(
            [
                "company_id",
                "bank_statement_line_id",
            ],
            [
                "bank_statement_lines.company_id",
                "bank_statement_lines.id",
            ],
            name=(
                "fk_bank_reconciliation_active_link_line"
            ),
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "company_id",
                "payment_id",
            ],
            [
                "payments.company_id",
                "payments.id",
            ],
            name=(
                "fk_bank_reconciliation_active_link_payment"
            ),
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "company_id",
                "reconciliation_id",
            ],
            [
                "bank_statement_reconciliations.company_id",
                "bank_statement_reconciliations.id",
            ],
            name=(
                "fk_bank_reconciliation_active_link_event"
            ),
            ondelete="RESTRICT",
        ),
        Index(
            "ix_bank_reconciliation_active_links_company_id",
            "company_id",
        ),
        Index(
            "ix_bank_reconciliation_active_links_line_id",
            "bank_statement_line_id",
        ),
        Index(
            "ix_bank_reconciliation_active_links_payment_id",
            "payment_id",
        ),
        Index(
            "ix_bank_reconciliation_active_links_reconciliation_id",
            "reconciliation_id",
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
    )

    bank_statement_line_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    payment_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    reconciliation_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
