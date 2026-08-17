from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AccountingPeriod(Base):
    __tablename__ = "accounting_periods"

    __table_args__ = (
    UniqueConstraint(
        "company_id",
        "year",
        "month",
        name="uq_accounting_period_company_year_month",
    ),
    CheckConstraint(
        "month >= 1 AND month <= 12",
        name="ck_accounting_period_month",
    ),
)

    id: Mapped[int] = mapped_column(
        primary_key=True
    )

    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id"),
        nullable=False,
        index=True,
    )

    year: Mapped[int] = mapped_column(
        nullable=False,
    )

    month: Mapped[int] = mapped_column(
        nullable=False,
    )

    start_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )

    end_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(20),
        default="open",
        nullable=False,
    )

    is_locked: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )

    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )