from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class PriceType(Base):
    """
    Company-scoped persistent price type master data.

    Examples:
        RETAIL
        WHOLESALE
        SUPPLIER
        INTERNAL

    Persistence only. It does not mutate TradeDocument prices,
    post accounting, or own transaction boundaries.
    """

    __tablename__ = "price_types"

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "id",
            name="uq_price_types_company_id_id",
        ),
        UniqueConstraint(
            "company_id",
            "code",
            name="uq_price_types_company_code",
        ),
        CheckConstraint(
            "length(trim(code)) > 0",
            name="ck_price_types_code_nonempty",
        ),
        CheckConstraint(
            "length(trim(name)) > 0",
            name="ck_price_types_name_nonempty",
        ),
        CheckConstraint(
            "kind IN ('sales', 'purchase', 'internal')",
            name="ck_price_types_kind",
        ),
        CheckConstraint(
            "char_length(currency_code) = 3",
            name="ck_price_types_currency_code_length",
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

    code: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    kind: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        index=True,
    )

    currency_code: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
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
