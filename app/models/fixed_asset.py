"""Canonical fixed-asset master and lifecycle identity."""

import enum
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class FixedAssetDepreciationMethod(str, enum.Enum):
    STRAIGHT_LINE = "straight_line"
    DIMINISHING_BALANCE = "diminishing_balance"
    DOUBLE_DIMINISHING_BALANCE = "double_diminishing_balance"
    CUMULATIVE = "cumulative"
    PRODUCTION = "production"


class FixedAssetStatus(str, enum.Enum):
    DRAFT = "draft"
    READY_FOR_COMMISSIONING = "ready_for_commissioning"
    IN_SERVICE = "in_service"
    SUSPENDED = "suspended"
    DISPOSED = "disposed"


class FixedAsset(Base):
    __tablename__ = "fixed_assets"

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "asset_number",
            name="uq_fixed_assets_company_asset_number",
        ),
        UniqueConstraint(
            "company_id",
            "id",
            name="uq_fixed_assets_company_id_id",
        ),
        ForeignKeyConstraint(
            ["company_id", "asset_account_id"],
            ["accounts.company_id", "accounts.id"],
            name="fk_fixed_assets_company_asset_account",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "accumulated_depreciation_account_id"],
            ["accounts.company_id", "accounts.id"],
            name="fk_fixed_assets_company_accum_depr_account",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "depreciation_expense_account_id"],
            ["accounts.company_id", "accounts.id"],
            name="fk_fixed_assets_company_depr_expense_account",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "original_cost >= 0",
            name="ck_fixed_assets_original_cost_nonnegative",
        ),
        CheckConstraint(
            "salvage_value >= 0",
            name="ck_fixed_assets_salvage_value_nonnegative",
        ),
        CheckConstraint(
            "salvage_value <= original_cost",
            name="ck_fixed_assets_salvage_not_above_cost",
        ),
        CheckConstraint(
            "useful_life_months > 0",
            name="ck_fixed_assets_useful_life_positive",
        ),
        CheckConstraint(
            "in_service_date IS NULL OR in_service_date >= acquisition_date",
            name="ck_fixed_assets_in_service_not_before_acquisition",
        ),
        Index(
            "ix_fixed_assets_company_status",
            "company_id",
            "status",
        ),
        Index(
            "ix_fixed_assets_asset_account_id",
            "asset_account_id",
        ),
        Index(
            "ix_fixed_assets_accumulated_depreciation_account_id",
            "accumulated_depreciation_account_id",
        ),
        Index(
            "ix_fixed_assets_depreciation_expense_account_id",
            "depreciation_expense_account_id",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="RESTRICT"),
        nullable=False,
    )

    asset_number: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    acquisition_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )

    in_service_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )

    original_cost: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
    )

    salvage_value: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
        default=Decimal("0.00"),
    )

    useful_life_months: Mapped[int] = mapped_column(
        nullable=False,
    )

    depreciation_method: Mapped[FixedAssetDepreciationMethod] = mapped_column(
        Enum(
            FixedAssetDepreciationMethod,
            name="fixed_asset_depreciation_method",
            native_enum=False,
            values_callable=lambda enum_cls: [item.value for item in enum_cls],
        ),
        nullable=False,
    )

    status: Mapped[FixedAssetStatus] = mapped_column(
        Enum(
            FixedAssetStatus,
            name="fixed_asset_status",
            native_enum=False,
            values_callable=lambda enum_cls: [item.value for item in enum_cls],
        ),
        nullable=False,
        default=FixedAssetStatus.DRAFT,
    )

    asset_account_id: Mapped[int] = mapped_column(
        nullable=False,
    )

    accumulated_depreciation_account_id: Mapped[int] = mapped_column(
        nullable=False,
    )

    depreciation_expense_account_id: Mapped[int] = mapped_column(
        nullable=False,
    )

    created_by: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
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
