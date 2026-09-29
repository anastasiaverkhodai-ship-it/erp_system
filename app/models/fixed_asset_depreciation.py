from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Index,
    text,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class FixedAssetDepreciation(Base):
    __tablename__ = "fixed_asset_depreciations"

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "request_key",
            name="uq_fa_depr_company_request",
        ),
        Index("ix_fa_depr_asset_period", "company_id", "fixed_asset_id", "period_start", "period_end"),
        Index("uq_fa_depr_reversal", "reversal_of_id", unique=True,
              postgresql_where=text("reversal_of_id IS NOT NULL")),
        CheckConstraint("actual_output IS NULL OR actual_output >= 0", name="ck_fa_depr_output_nonnegative"),
        CheckConstraint("expected_output IS NULL OR expected_output > 0", name="ck_fa_depr_expected_positive"),
        CheckConstraint(
            "period_end >= period_start",
            name="ck_fa_depr_period_order",
        ),
        CheckConstraint(
            "amount > 0 OR (amount = 0 AND method = 'production' AND actual_output IS NOT NULL)",
            name="ck_fa_depr_amount_positive",
        ),
        CheckConstraint(
            "accumulated_before >= 0",
            name="ck_fa_depr_accum_before_nonnegative",
        ),
        CheckConstraint(
            "(reversal_of_id IS NULL AND accumulated_after >= accumulated_before) OR "
            "(reversal_of_id IS NOT NULL AND accumulated_after <= accumulated_before)",
            name="ck_fa_depr_accum_order",
        ),
    )

    actual_output: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    expected_output: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    company_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey(
            "companies.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
        index=True,
    )

    fixed_asset_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey(
            "fixed_assets.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
        index=True,
    )

    request_key: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    period_start: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )

    period_end: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )

    posting_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
        index=True,
    )

    method: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
    )

    amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
    )

    accumulated_before: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
    )

    accumulated_after: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
    )

    reversal_of_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey(
            "fixed_asset_depreciations.id",
            ondelete="RESTRICT",
        ),
        nullable=True,
        index=True,
    )

    created_by: Mapped[int] = mapped_column(
        Integer,
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
