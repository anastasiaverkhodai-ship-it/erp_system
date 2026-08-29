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
from app.services.payment_types import (
    PaymentDirection,
    PaymentStatus,
)


class Payment(Base):
    """
    Persistent commercial money-movement document.

    STEP 16A boundary:
      - no Warehouse Document
      - no Stock movement
      - no JournalEntry FK
      - no AccountingRule FK
      - no VAT recognition event yet

    Settlement against AR/AP obligations is represented separately by
    PaymentSettlementAllocation.
    """

    __tablename__ = "payments"

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "id",
            name="uq_payments_company_id_id",
        ),
        UniqueConstraint(
            "company_id",
            "direction",
            "number",
            name=(
                "uq_payments_company_direction_number"
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
                "fk_payments_company_counterparty"
            ),
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
            name=(
                "fk_payments_company_counterparty_contract"
            ),
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "direction IN ('incoming', 'outgoing')",
            name="ck_payments_direction",
        ),
        CheckConstraint(
            (
                "status IN ("
                "'draft', "
                "'confirmed', "
                "'cancelled'"
                ")"
            ),
            name="ck_payments_status",
        ),
        CheckConstraint(
            "amount > 0",
            name="ck_payments_amount_positive",
        ),
        CheckConstraint(
            "char_length(currency_code) = 3",
            name="ck_payments_currency_code_length",
        ),
        CheckConstraint(
            (
                "("
                "status = 'draft' "
                "AND confirmed_at IS NULL "
                "AND cancelled_by IS NULL "
                "AND cancelled_at IS NULL"
                ") OR ("
                "status = 'confirmed' "
                "AND confirmed_at IS NOT NULL "
                "AND cancelled_by IS NULL "
                "AND cancelled_at IS NULL"
                ") OR ("
                "status = 'cancelled' "
                "AND cancelled_by IS NOT NULL "
                "AND cancelled_at IS NOT NULL"
                ")"
            ),
            name="ck_payments_lifecycle_state",
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

    contract_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        index=True,
    )

    number: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    direction: Mapped[
        PaymentDirection
    ] = mapped_column(
        SQLEnum(
            PaymentDirection,
            name="payment_direction_enum",
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
        index=True,
    )

    status: Mapped[
        PaymentStatus
    ] = mapped_column(
        SQLEnum(
            PaymentStatus,
            name="payment_status_enum",
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            values_callable=lambda enum_class: [
                item.value
                for item in enum_class
            ],
            length=20,
        ),
        default=PaymentStatus.DRAFT,
        nullable=False,
        index=True,
    )

    payment_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
        index=True,
    )

    currency_code: Mapped[str] = mapped_column(
        String(3),
        default="UAH",
        nullable=False,
        index=True,
    )

    amount: Mapped[Decimal] = mapped_column(
        Numeric(
            precision=18,
            scale=2,
        ),
        nullable=False,
    )

    external_reference: Mapped[
        str | None
    ] = mapped_column(
        String(255),
        nullable=True,
        index=True,
    )

    description: Mapped[
        str | None
    ] = mapped_column(
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

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    confirmed_at: Mapped[
        datetime | None
    ] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    cancelled_by: Mapped[
        int | None
    ] = mapped_column(
        ForeignKey(
            "users.id",
            ondelete="RESTRICT",
        ),
        nullable=True,
    )

    cancelled_at: Mapped[
        datetime | None
    ] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
