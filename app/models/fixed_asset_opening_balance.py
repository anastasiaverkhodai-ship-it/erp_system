"""Fixed-asset domain state attached to the canonical opening-balance lifecycle.

Accounting money remains exclusively in JournalEntryLine.  This table stores
the fixed-asset opening state needed by the FA subledger and by later
depreciation processing.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
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


class FixedAssetOpeningBalance(Base):
    __tablename__ = "fixed_asset_opening_balances"

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "opening_balance_id",
            "fixed_asset_id",
            name="uq_fa_opening_source_asset",
        ),
        ForeignKeyConstraint(
            ["company_id", "opening_balance_id"],
            ["opening_balances.company_id", "opening_balances.id"],
            name="fk_fa_opening_source",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "original_cost > 0",
            name="ck_fa_opening_original_cost_positive",
        ),
        CheckConstraint(
            "accumulated_depreciation >= 0",
            name="ck_fa_opening_accumulated_depreciation_nonnegative",
        ),
        CheckConstraint(
            "accumulated_depreciation <= original_cost",
            name="ck_fa_opening_accumulated_depreciation_not_above_cost",
        ),
        CheckConstraint(
            "in_service_date >= acquisition_date",
            name="ck_fa_opening_service_not_before_acquisition",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    company_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    opening_balance_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    fixed_asset_id: Mapped[int] = mapped_column(
        ForeignKey(
            "fixed_assets.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
        index=True,
    )

    acquisition_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )

    in_service_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )

    original_cost: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
    )

    accumulated_depreciation: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
        default=Decimal("0.00"),
    )

    previous_status: Mapped[str] = mapped_column(
        String(23),
        nullable=False,
    )

    previous_acquisition_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )

    previous_in_service_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )

    previous_original_cost: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
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
