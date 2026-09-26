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


class FixedAssetAcquisitionCostType(str, enum.Enum):
    ACQUISITION = "acquisition"
    DELIVERY = "delivery"
    INSTALLATION = "installation"
    OTHER_DIRECT = "other_direct"


class FixedAssetAcquisitionCost(Base):
    __tablename__ = "fixed_asset_acquisition_costs"

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "id",
            name="uq_faac_company_id_id",
        ),
        UniqueConstraint(
            "company_id",
            "request_key",
            name="uq_faac_company_request_key",
        ),
        ForeignKeyConstraint(
            ["company_id", "fixed_asset_id"],
            ["fixed_assets.company_id", "fixed_assets.id"],
            name="fk_faac_company_asset",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "reversal_of_id"],
            [
                "fixed_asset_acquisition_costs.company_id",
                "fixed_asset_acquisition_costs.id",
            ],
            name="fk_faac_company_reversal_source",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "amount > 0",
            name="ck_faac_amount_positive",
        ),
        CheckConstraint(
            "reversal_of_id IS NULL OR reversal_of_id <> id",
            name="ck_faac_not_self_reversal",
        ),
        CheckConstraint(
            "char_length(trim(request_key)) > 0",
            name="ck_faac_request_key_nonempty",
        ),
        Index(
            "ix_faac_company_asset",
            "company_id",
            "fixed_asset_id",
            "id",
        ),
        Index(
            "ix_faac_source_line",
            "source_journal_entry_line_id",
        ),
        Index(
            "uq_faac_reversal_of",
            "reversal_of_id",
            unique=True,
            postgresql_where=text("reversal_of_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    fixed_asset_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    request_key: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    cost_type: Mapped[FixedAssetAcquisitionCostType] = mapped_column(
        SQLEnum(
            FixedAssetAcquisitionCostType,
            name="fixed_asset_acquisition_cost_type",
            values_callable=lambda enum_cls: [item.value for item in enum_cls],
        ),
        nullable=False,
    )

    recognition_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )

    amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
    )

    source_journal_entry_line_id: Mapped[int] = mapped_column(
        ForeignKey(
            "journal_entry_lines.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )

    source_description: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
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
