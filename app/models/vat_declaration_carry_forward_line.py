from __future__ import annotations

from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
    Integer,
    Numeric,
    UniqueConstraint,
    Index,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class VatDeclarationCarryForwardLine(Base):
    __tablename__ = "vat_declaration_carry_forward_lines"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )
    company_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False)
    vat_declaration_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    source_declaration_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    origin_reporting_year: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    origin_reporting_month: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    opening_amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
    )
    consumed_amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
        default=Decimal("0.00"),
    )
    closing_amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
    )

    __table_args__ = (
        Index("ix_vdcf_company", "company_id"),
        ForeignKeyConstraint(
            ["company_id", "vat_declaration_id"],
            ["vat_declarations.company_id", "vat_declarations.id"],
            name="fk_vdcf_decl_tenant",
        ),
        ForeignKeyConstraint(
            ["company_id", "source_declaration_id"],
            ["vat_declarations.company_id", "vat_declarations.id"],
            name="fk_vdcf_source_tenant",
        ),
        UniqueConstraint(
            "company_id",
            "id",
            name="uq_vdcf_company_id",
        ),
        UniqueConstraint(
            "vat_declaration_id",
            "origin_reporting_year",
            "origin_reporting_month",
            name="uq_vdcf_decl_origin",
        ),
        CheckConstraint(
            "origin_reporting_month BETWEEN 1 AND 12",
            name="ck_vdcf_origin_month",
        ),
        CheckConstraint(
            "opening_amount >= 0 "
            "AND consumed_amount >= 0 "
            "AND closing_amount >= 0",
            name="ck_vdcf_nonneg",
        ),
        CheckConstraint(
            "consumed_amount <= opening_amount",
            name="ck_vdcf_consumed",
        ),
        CheckConstraint(
            "closing_amount = opening_amount - consumed_amount",
            name="ck_vdcf_arithmetic",
        ),
        CheckConstraint(
            "source_declaration_id <> vat_declaration_id",
            name="ck_vdcf_not_self",
        ),
    )
