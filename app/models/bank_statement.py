from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class BankStatement(Base):
    """
    Immutable-origin bank statement container.

    This is external banking evidence metadata only.
    It does not represent a Payment and does not post GL.
    """

    __tablename__ = "bank_statements"

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "id",
            name="uq_bank_statements_company_id_id",
        ),
        UniqueConstraint(
            "company_id",
            "id",
            "bank_account_id",
            name=(
                "uq_bank_statements_"
                "company_id_id_bank_account_id"
            ),
        ),
        UniqueConstraint(
            "company_id",
            "bank_account_id",
            "external_id",
            name=(
                "uq_bank_statements_"
                "company_account_external"
            ),
        ),
        ForeignKeyConstraint(
            [
                "company_id",
                "bank_account_id",
            ],
            [
                "bank_accounts.company_id",
                "bank_accounts.id",
            ],
            name="fk_bank_statements_company_bank_account",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "length(trim(external_id)) > 0",
            name="ck_bank_statements_external_id_nonempty",
        ),
        CheckConstraint(
            (
                "period_end IS NULL "
                "OR period_start IS NULL "
                "OR period_end >= period_start"
            ),
            name="ck_bank_statements_period_order",
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

    bank_account_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    external_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    statement_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
        index=True,
    )

    period_start: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )

    period_end: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )

    source_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="manual",
        server_default="manual",
    )

    source_reference: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )

    created_by: Mapped[int | None] = mapped_column(
        ForeignKey(
            "users.id",
            ondelete="RESTRICT",
        ),
        nullable=True,
        index=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
