from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum as SQLEnum,
    ForeignKey,
    ForeignKeyConstraint,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.services.account_types import (
    AccountNormalBalance,
    AccountType,
)


class Account(Base):
    __tablename__ = "accounts"

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "code",
            name="uq_account_company_code",
        ),
        UniqueConstraint(
            "company_id",
            "id",
            name="uq_accounts_company_id_id",
        ),
        ForeignKeyConstraint(
            [
                "company_id",
                "parent_id",
            ],
            [
                "accounts.company_id",
                "accounts.id",
            ],
            name="fk_accounts_company_parent",
        ),
        CheckConstraint(
            (
                "account_type IN ("
                "'asset', "
                "'liability', "
                "'equity', "
                "'income', "
                "'expense', "
                "'off_balance'"
                ")"
            ),
            name="account_type_enum",
        ),
        CheckConstraint(
            (
                "normal_balance IN ("
                "'debit', "
                "'credit', "
                "'debit_credit'"
                ")"
            ),
            name="account_normal_balance_enum",
        ),
    )

    id: Mapped[int] = mapped_column(
        primary_key=True,
    )

    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id"),
        nullable=False,
        index=True,
    )

    code: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )

    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    account_type: Mapped[AccountType] = mapped_column(
        SQLEnum(
            AccountType,
            name="account_type_enum",
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            values_callable=lambda enum_class: [
                item.value
                for item in enum_class
            ],
            length=50,
        ),
        nullable=False,
    )

    normal_balance: Mapped[
        AccountNormalBalance
    ] = mapped_column(
        SQLEnum(
            AccountNormalBalance,
            name="account_normal_balance_enum",
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
    )

    parent_id: Mapped[int | None] = mapped_column(
        nullable=True,
    )

    is_postable: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )

    is_system: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )
