from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
    Index,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class VatDeclaration(Base):
    __tablename__ = "vat_declarations"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )
    company_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False)

    reporting_year: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    reporting_month: Mapped[int] = mapped_column(
        Integer,
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

    source_cutoff_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    snapshot_version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    supersedes_declaration_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    output_taxable_base: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
        default=Decimal("0.00"),
    )
    output_vat: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
        default=Decimal("0.00"),
    )
    input_taxable_base: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
        default=Decimal("0.00"),
    )
    input_vat_credit: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
        default=Decimal("0.00"),
    )

    opening_negative_carry: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
        default=Decimal("0.00"),
    )
    vat_payable: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
        default=Decimal("0.00"),
    )
    current_period_negative: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
        default=Decimal("0.00"),
    )
    closing_negative_carry: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
        default=Decimal("0.00"),
    )

    currency_code: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
        default="UAH",
    )

    created_by: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    source_lines = relationship("VatDeclarationSourceLine", viewonly=True,
        primaryjoin="and_(VatDeclaration.company_id == VatDeclarationSourceLine.company_id, VatDeclaration.id == VatDeclarationSourceLine.vat_declaration_id)", order_by="VatDeclarationSourceLine.line_number")
    carry_forward_lines = relationship("VatDeclarationCarryForwardLine", viewonly=True,
        primaryjoin="and_(VatDeclaration.company_id == VatDeclarationCarryForwardLine.company_id, VatDeclaration.id == VatDeclarationCarryForwardLine.vat_declaration_id)",
        foreign_keys="[VatDeclarationCarryForwardLine.company_id, VatDeclarationCarryForwardLine.vat_declaration_id]", order_by="VatDeclarationCarryForwardLine.id")
    status_events = relationship("VatDeclarationStatusEvent", viewonly=True,
        primaryjoin="and_(VatDeclaration.company_id == VatDeclarationStatusEvent.company_id, VatDeclaration.id == VatDeclarationStatusEvent.vat_declaration_id)", order_by="VatDeclarationStatusEvent.id")

    __table_args__ = (
        Index("ix_vd_company", "company_id"),
        ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name="fk_vd_company",
        ),
        ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name="fk_vd_created_by",
        ),
        ForeignKeyConstraint(
            ["company_id", "supersedes_declaration_id"],
            ["vat_declarations.company_id", "vat_declarations.id"],
            name="fk_vd_supersedes_tenant",
        ),
        UniqueConstraint(
            "company_id",
            "id",
            name="uq_vd_company_id",
        ),
        UniqueConstraint(
            "company_id",
            "reporting_year",
            "reporting_month",
            "snapshot_version",
            name="uq_vd_period_version",
        ),
        CheckConstraint(
            "reporting_month BETWEEN 1 AND 12",
            name="ck_vd_month",
        ),
        CheckConstraint(
            "snapshot_version >= 1",
            name="ck_vd_version",
        ),
        CheckConstraint(
            "period_start <= period_end",
            name="ck_vd_period_order",
        ),
        CheckConstraint(
            "currency_code = 'UAH'",
            name="ck_vd_uah",
        ),
        CheckConstraint(
            "opening_negative_carry >= 0 "
            "AND vat_payable >= 0 "
            "AND current_period_negative >= 0 "
            "AND closing_negative_carry >= 0",
            name="ck_vd_amounts_nonneg",
        ),
        CheckConstraint(
            "vat_payable - closing_negative_carry = output_vat - input_vat_credit - opening_negative_carry",
            name="ck_vd_net_balance",
        ),
        CheckConstraint(
            "supersedes_declaration_id IS NULL "
            "OR supersedes_declaration_id <> id",
            name="ck_vd_no_self_supersede",
        ),
    )
