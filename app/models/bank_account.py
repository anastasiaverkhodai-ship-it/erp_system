from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
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


class BankAccount(Base):
    """
    Company-scoped operational bank account.

    This is master data only.

    BankAccount does not represent a Payment, does not post GL,
    and does not own transaction boundaries.
    """

    __tablename__ = "bank_accounts"

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "id",
            name="uq_bank_accounts_company_id_id",
        ),
        UniqueConstraint(
            "company_id",
            "account_number",
            name="uq_bank_accounts_company_account_number",
        ),
        ForeignKeyConstraint(
            [
                "company_id",
                "accounting_account_id",
            ],
            [
                "accounts.company_id",
                "accounts.id",
            ],
            name="fk_bank_accounts_company_account",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "length(trim(name)) > 0",
            name="ck_bank_accounts_name_nonempty",
        ),
        CheckConstraint(
            "length(trim(account_number)) > 0",
            name="ck_bank_accounts_number_nonempty",
        ),
        CheckConstraint(
            "char_length(currency_code) = 3",
            name="ck_bank_accounts_currency_code_length",
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

    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    account_number: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    currency_code: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
        default="UAH",
        server_default="UAH",
        index=True,
    )

    accounting_account_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
        index=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
