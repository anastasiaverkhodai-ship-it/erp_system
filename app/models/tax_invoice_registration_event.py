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
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class TaxInvoiceRegistrationEvent(Base):
    """
    Immutable registration-history row.

    Current registration state is derived from ordered event history.
    The TaxInvoice header itself has no mutable registration status.

    External ЄРПН transport and KEP verification are intentionally
    outside this V1 model and remain an integration concern.
    """

    __tablename__ = "tax_invoice_registration_events"

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "id",
            name="uq_tire_company_id",
        ),
        ForeignKeyConstraint(
            [
                "company_id",
                "tax_invoice_id",
            ],
            [
                "tax_invoices.company_id",
                "tax_invoices.id",
            ],
            name="fk_tire_invoice",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "status IN "
            "('prepared', 'submitted', 'registered', "
            "'suspended', 'rejected')",
            name="ck_tire_status",
        ),
        CheckConstraint(
            "status = 'prepared' "
            "OR (reference IS NOT NULL "
            "AND length(trim(reference)) > 0)",
            name="ck_tire_reference",
        ),
        Index(
            "ix_tire_invoice_date",
            "company_id",
            "tax_invoice_id",
            "event_date",
            "id",
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
    )

    tax_invoice_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )

    event_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )

    reference: Mapped[str | None] = mapped_column(
        String(500),
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
        server_default=func.now(),
        nullable=False,
    )
