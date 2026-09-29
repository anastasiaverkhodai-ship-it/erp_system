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


class FixedAssetRepairImprovementType(str, enum.Enum):
    REPAIR = "repair"
    IMPROVEMENT = "improvement"


class FixedAssetRepairImprovement(Base):
    __tablename__ = "fixed_asset_repair_improvements"

    __table_args__ = (
        CheckConstraint('new_remaining_life_months IS NULL OR new_remaining_life_months > 0', name='ck_fari_remaining_life'),
        UniqueConstraint(
            "company_id",
            "id",
            name="uq_fari_company_id_id",
        ),
        UniqueConstraint(
            "company_id",
            "request_key",
            name="uq_fari_company_request_key",
        ),
        ForeignKeyConstraint(
            ["company_id", "fixed_asset_id"],
            ["fixed_assets.company_id", "fixed_assets.id"],
            name="fk_fari_company_asset",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["source_journal_entry_line_id"],
            ["journal_entry_lines.id"],
            name="fk_fari_source_journal_entry_line",
        ),
        ForeignKeyConstraint(
            ["company_id", "reversal_of_id"],
            [
                "fixed_asset_repair_improvements.company_id",
                "fixed_asset_repair_improvements.id",
            ],
            name="fk_fari_company_reversal",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "amount > 0",
            name="ck_fari_amount_positive",
        ),
        Index(
            "ix_fari_company_asset",
            "company_id",
            "fixed_asset_id",
        ),
        Index(
            "ix_fari_source_line",
            "source_journal_entry_line_id",
        ),
        Index(
            "uq_fari_reversal_of",
            "reversal_of_id",
            unique=True,
            postgresql_where=text("reversal_of_id IS NOT NULL"),
        ),
    )

    new_remaining_life_months: Mapped[int | None] = mapped_column(Integer, nullable=True)
    useful_life_months_before: Mapped[int | None] = mapped_column(Integer, nullable=True)
    depreciable_base_after: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    original_cost_after: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    accumulated_at_change: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)

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

    operation_type: Mapped[FixedAssetRepairImprovementType] = mapped_column(
        SQLEnum(
            FixedAssetRepairImprovementType,
            name="fixed_asset_repair_improvement_type_enum",
            native_enum=False,
            create_constraint=False,
            validate_strings=True,
            values_callable=lambda enum_class: [item.value for item in enum_class],
            length=20,
        ),
        nullable=False,
    )

    operation_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )

    amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
    )

    source_journal_entry_line_id: Mapped[int] = mapped_column(
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
