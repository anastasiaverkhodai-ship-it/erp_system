from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class FixedAssetDisposal(Base):
    __tablename__ = "fixed_asset_disposals"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )
    company_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    fixed_asset_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    disposal_date: Mapped[date] = mapped_column(
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
    )
    carrying_amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
    )
    salvage_value: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
    )

    previous_status: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
    )

    disposal_account_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    request_key: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )
    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    journal_entry_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    reversal_of_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    created_by: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
    )

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "id",
            name="uq_fad_company_id_id",
        ),
        UniqueConstraint(
            "company_id",
            "request_key",
            name="uq_fad_company_request_key",
        ),
        UniqueConstraint(
            "reversal_of_id",
            name="uq_fad_reversal_of",
        ),
        ForeignKeyConstraint(
            ["company_id", "fixed_asset_id"],
            ["fixed_assets.company_id", "fixed_assets.id"],
            name="fk_fad_company_asset",
        ),
        ForeignKeyConstraint(
            ["company_id", "disposal_account_id"],
            ["accounts.company_id", "accounts.id"],
            name="fk_fad_company_disposal_account",
        ),
        ForeignKeyConstraint(
            ["company_id", "reversal_of_id"],
            ["fixed_asset_disposals.company_id", "fixed_asset_disposals.id"],
            name="fk_fad_company_reversal",
        ),
        CheckConstraint(
            "original_cost >= 0",
            name="ck_fad_original_cost_nonnegative",
        ),
        CheckConstraint(
            "accumulated_depreciation >= 0",
            name="ck_fad_accumulated_nonnegative",
        ),
        CheckConstraint(
            "carrying_amount >= 0",
            name="ck_fad_carrying_nonnegative",
        ),
        CheckConstraint(
            "salvage_value >= 0",
            name="ck_fad_salvage_nonnegative",
        ),
        CheckConstraint(
            "accumulated_depreciation <= original_cost",
            name="ck_fad_accumulated_le_cost",
        ),
        Index(
            "ix_fad_company_asset",
            "company_id",
            "fixed_asset_id",
        ),
    )
