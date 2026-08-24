from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum as SQLEnum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.services.counterparty_types import (
    CounterpartyType,
    CounterpartyVatStatus,
)


class Counterparty(Base):
    __tablename__ = "counterparties"

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "edrpou",
            name="uq_counterparty_company_edrpou",
        ),
        UniqueConstraint(
            "company_id",
            "tax_number",
            name="uq_counterparty_company_tax_number",
        ),
        UniqueConstraint(
            "company_id",
            "vat_number",
            name="uq_counterparty_company_vat_number",
        ),
        CheckConstraint(
            (
                "counterparty_type IN "
                "('customer', 'supplier', 'both')"
            ),
            name="counterparty_type_enum",
        ),
        CheckConstraint(
            (
                "vat_status IN "
                "('unknown', 'non_vat_payer', "
                "'vat_payer')"
            ),
            name="counterparty_vat_status_enum",
        ),
        CheckConstraint(
            "payment_term_days >= 0",
            name="ck_counterparty_payment_term_days_nonnegative",
        ),
        CheckConstraint(
            "credit_limit >= 0",
            name="ck_counterparty_credit_limit_nonnegative",
        ),
        CheckConstraint(
            "char_length(default_currency_code) = 3",
            name="ck_counterparty_currency_code_length",
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

    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    short_name: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    counterparty_type: Mapped[
        CounterpartyType
    ] = mapped_column(
        SQLEnum(
            CounterpartyType,
            name="counterparty_type_enum",
            native_enum=False,
            create_constraint=False,
            values_callable=lambda enum: [
                item.value
                for item in enum
            ],
        ),
        nullable=False,
        default=CounterpartyType.BOTH,
        server_default=CounterpartyType.BOTH.value,
    )

    edrpou: Mapped[str | None] = mapped_column(
        String(8),
        nullable=True,
    )

    tax_number: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
    )

    vat_status: Mapped[
        CounterpartyVatStatus
    ] = mapped_column(
        SQLEnum(
            CounterpartyVatStatus,
            name="counterparty_vat_status_enum",
            native_enum=False,
            create_constraint=False,
            values_callable=lambda enum: [
                item.value
                for item in enum
            ],
        ),
        nullable=False,
        default=CounterpartyVatStatus.UNKNOWN,
        server_default=(
            CounterpartyVatStatus.UNKNOWN.value
        ),
    )

    vat_number: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
    )

    default_currency_code: Mapped[str] = mapped_column(
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

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
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
