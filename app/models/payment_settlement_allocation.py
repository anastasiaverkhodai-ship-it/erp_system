from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum as SQLEnum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.services.payment_types import (
    PaymentSettlementAllocationStatus,
)


class PaymentSettlementAllocation(Base):
    """
    Persistent monetary allocation between one confirmed Payment
    and one CounterpartyOpenItem.

    ACTIVE rows participate in settlement balances.
    REVERSED rows remain permanently for audit history.

    No hard delete is part of the business lifecycle.
    """

    __tablename__ = (
        "payment_settlement_allocations"
    )

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "id",
            name=(
                "uq_payment_settlement_allocations_"
                "company_id_id"
            ),
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
                "fk_payment_settlement_allocations_"
                "payment"
            ),
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "company_id",
                "open_item_id",
            ],
            [
                "counterparty_open_items.company_id",
                "counterparty_open_items.id",
            ],
            name=(
                "fk_payment_settlement_allocations_"
                "open_item"
            ),
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "amount > 0",
            name=(
                "ck_payment_settlement_allocations_"
                "amount_positive"
            ),
        ),
        CheckConstraint(
            "status IN ('active', 'reversed')",
            name=(
                "ck_payment_settlement_allocations_"
                "status"
            ),
        ),
        CheckConstraint(
            (
                "("
                "status = 'active' "
                "AND reversed_by IS NULL "
                "AND reversed_at IS NULL"
                ") OR ("
                "status = 'reversed' "
                "AND reversed_by IS NOT NULL "
                "AND reversed_at IS NOT NULL"
                ")"
            ),
            name=(
                "ck_payment_settlement_allocations_"
                "reversal_state"
            ),
        ),
        Index(
            "ix_payment_settlement_payment_active",
            "company_id",
            "payment_id",
            "id",
            postgresql_where=text(
                "status = 'active'"
            ),
        ),
        Index(
            "ix_payment_settlement_open_item_active",
            "company_id",
            "open_item_id",
            "id",
            postgresql_where=text(
                "status = 'active'"
            ),
        ),
        Index(
            "uq_payment_settlement_active_pair",
            "company_id",
            "payment_id",
            "open_item_id",
            unique=True,
            postgresql_where=text(
                "status = 'active'"
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

    payment_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    open_item_id: Mapped[int] = mapped_column(
        Integer,
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

    status: Mapped[
        PaymentSettlementAllocationStatus
    ] = mapped_column(
        SQLEnum(
            PaymentSettlementAllocationStatus,
            name=(
                "payment_settlement_"
                "allocation_status_enum"
            ),
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            values_callable=lambda enum_class: [
                item.value
                for item in enum_class
            ],
            length=20,
        ),
        default=(
            PaymentSettlementAllocationStatus.ACTIVE
        ),
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
        nullable=False,
        server_default=func.now(),
    )

    reversed_by: Mapped[
        int | None
    ] = mapped_column(
        ForeignKey(
            "users.id",
            ondelete="RESTRICT",
        ),
        nullable=True,
    )

    reversed_at: Mapped[
        datetime | None
    ] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
