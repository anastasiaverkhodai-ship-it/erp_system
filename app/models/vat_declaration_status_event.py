from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    String,
    UniqueConstraint,
    func,
    Index,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class VatDeclarationStatusEvent(Base):
    __tablename__ = "vat_declaration_status_events"

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

    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
    )
    event_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )
    reference: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
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

    __table_args__ = (
        Index("ix_vdse_company", "company_id"),
        ForeignKeyConstraint(
            ["company_id", "vat_declaration_id"],
            ["vat_declarations.company_id", "vat_declarations.id"],
            name="fk_vdse_decl_tenant",
        ),
        ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name="fk_vdse_created_by",
        ),
        UniqueConstraint(
            "company_id",
            "id",
            name="uq_vdse_company_id",
        ),
        CheckConstraint(
            "status IN ("
            "'prepared', "
            "'finalized', "
            "'submitted', "
            "'accepted', "
            "'rejected'"
            ")",
            name="ck_vdse_status",
        ),
        CheckConstraint(
            "status NOT IN ('submitted', 'accepted', 'rejected') "
            "OR (reference IS NOT NULL AND btrim(reference) <> '')",
            name="ck_vdse_reference",
        ),
    )
