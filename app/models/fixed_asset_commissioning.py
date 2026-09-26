from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class FixedAssetCommissioning(Base):
    __tablename__ = "fixed_asset_commissionings"

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "id",
            name="uq_facomm_company_id",
        ),
        UniqueConstraint(
            "company_id",
            "request_key",
            name="uq_facomm_company_request_key",
        ),
        ForeignKeyConstraint(
            ["company_id", "fixed_asset_id"],
            ["fixed_assets.company_id", "fixed_assets.id"],
            name="fk_facomm_company_asset",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "reversal_of_id"],
            [
                "fixed_asset_commissionings.company_id",
                "fixed_asset_commissionings.id",
            ],
            name="fk_facomm_company_reversal",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "char_length(trim(request_key)) > 0",
            name="ck_facomm_request_key_nonempty",
        ),
        CheckConstraint(
            "reversal_of_id IS NULL OR reversal_of_id <> id",
            name="ck_facomm_no_self_reversal",
        ),
        Index(
            "ix_facomm_company_asset",
            "company_id",
            "fixed_asset_id",
        ),
        Index(
            "uq_facomm_reversal_of",
            "reversal_of_id",
            unique=True,
            postgresql_where=text("reversal_of_id IS NOT NULL"),
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

    fixed_asset_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    request_key: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    commissioning_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )

    reversal_of_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
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
