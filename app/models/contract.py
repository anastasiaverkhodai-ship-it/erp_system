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
from app.services.contract_types import (
    ContractStatus,
    ContractType,
)


class Contract(Base):
    __tablename__ = "contracts"

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "id",
            name="uq_contracts_company_id_id",
        ),
        UniqueConstraint(
            "company_id",
            "counterparty_id",
            "number",
            name=(
                "uq_contract_company_"
                "counterparty_number"
            ),
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
                "fk_contracts_company_"
                "counterparty"
            ),
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            (
                "contract_type IN "
                "('sales', 'purchase', 'mixed')"
            ),
            name="contract_type_enum",
        ),
        CheckConstraint(
            (
                "status IN "
                "('draft', 'active', 'closed')"
            ),
            name="contract_status_enum",
        ),
        CheckConstraint(
            (
                "end_date IS NULL "
                "OR end_date >= start_date"
            ),
            name="ck_contract_date_range",
        ),
        CheckConstraint(
            "payment_term_days >= 0",
            name=(
                "ck_contract_payment_term_"
                "days_nonnegative"
            ),
        ),
        CheckConstraint(
            "credit_limit >= 0",
            name=(
                "ck_contract_credit_limit_"
                "nonnegative"
            ),
        ),
        CheckConstraint(
            "char_length(currency_code) = 3",
            name=(
                "ck_contract_currency_code_"
                "length"
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

    counterparty_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    number: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    name: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    contract_type: Mapped[
        ContractType
    ] = mapped_column(
        SQLEnum(
            ContractType,
            name="contract_type_enum",
            native_enum=False,
            create_constraint=False,
            values_callable=lambda enum: [
                item.value
                for item in enum
            ],
        ),
        nullable=False,
    )

    status: Mapped[
        ContractStatus
    ] = mapped_column(
        SQLEnum(
            ContractStatus,
            name="contract_status_enum",
            native_enum=False,
            create_constraint=False,
            values_callable=lambda enum: [
                item.value
                for item in enum
            ],
        ),
        nullable=False,
        default=ContractStatus.DRAFT,
        server_default=(
            ContractStatus.DRAFT.value
        ),
    )

    start_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )

    end_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )

    currency_code: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
        default="UAH",
        server_default="UAH",
    )

    payment_term_days: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )

    credit_limit: Mapped[Decimal] = mapped_column(
        Numeric(
            precision=18,
            scale=2,
        ),
        nullable=False,
        default=Decimal("0.00"),
        server_default="0.00",
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
