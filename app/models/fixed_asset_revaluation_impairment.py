import enum
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Enum as SQLEnum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class FixedAssetRevaluationImpairmentType(str, enum.Enum):
    REVALUATION = "revaluation"
    IMPAIRMENT = "impairment"


class FixedAssetRevaluationImpairment(Base):
    __tablename__ = "fixed_asset_revaluation_impairments"

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "id",
            name="uq_fari2_company_id_id",
        ),
        UniqueConstraint(
            "company_id",
            "request_key",
            name="uq_fari2_company_request_key",
        ),
        ForeignKeyConstraint(
            ["company_id", "fixed_asset_id"],
            ["fixed_assets.company_id", "fixed_assets.id"],
            name="fk_fari2_company_asset",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "counterpart_account_id"],
            ["accounts.company_id", "accounts.id"],
            name="fk_fari2_company_counterpart_account",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "reversal_of_id"],
            [
                "fixed_asset_revaluation_impairments.company_id",
                "fixed_asset_revaluation_impairments.id",
            ],
            name="fk_fari2_company_reversal",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "carrying_amount_before >= 0",
            name="ck_fari2_carrying_before_nonnegative",
        ),
        CheckConstraint(
            "carrying_amount_after >= 0",
            name="ck_fari2_carrying_after_nonnegative",
        ),
        CheckConstraint(
            "amount > 0",
            name="ck_fari2_amount_positive",
        ),
        CheckConstraint(
            "original_cost_before >= 0",
            name="ck_fari2_original_before_nonnegative",
        ),
        CheckConstraint(
            "original_cost_after >= 0",
            name="ck_fari2_original_after_nonnegative",
        ),
        Index(
            "ix_fari2_company_asset",
            "company_id",
            "fixed_asset_id",
        ),
        Index(
            "uq_fari2_reversal_of",
            "reversal_of_id",
            unique=True,
            postgresql_where=text("reversal_of_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    fixed_asset_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    operation_type: Mapped[
        FixedAssetRevaluationImpairmentType
    ] = mapped_column(
        SQLEnum(
            FixedAssetRevaluationImpairmentType,
            name="fixed_asset_revaluation_impairment_type_enum",
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            values_callable=lambda cls: [item.value for item in cls],
            length=20,
        ),
        nullable=False,
    )

    operation_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )

    carrying_amount_before: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
    )

    carrying_amount_after: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
    )

    amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
    )

    accumulated_depreciation: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
    )

    original_cost_before: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
    )

    original_cost_after: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
    )

    counterpart_account_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    request_key: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    description: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )

    journal_entry_id: Mapped[int | None] = mapped_column(
        ForeignKey("journal_entries.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )

    reversal_of_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
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
