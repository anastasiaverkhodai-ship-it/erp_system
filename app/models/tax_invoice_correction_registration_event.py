from datetime import date, datetime, timezone

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
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class TaxInvoiceCorrectionRegistrationEvent(Base):
    """
    Immutable RK registration-history row.

    Current RK registration state is derived from ordered event history.
    The RK header has no mutable registration status.
    """

    __tablename__ = "tax_invoice_correction_registration_events"

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "id",
            name="uq_ticre_company_id",
        ),
        ForeignKeyConstraint(
            [
                "company_id",
                "tax_invoice_correction_id",
            ],
            [
                "tax_invoice_corrections.company_id",
                "tax_invoice_corrections.id",
            ],
            name="fk_ticre_correction",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            """
            status IN (
                'prepared',
                'submitted',
                'registered',
                'suspended',
                'rejected'
            )
            """,
            name="ck_ticre_status",
        ),
        CheckConstraint(
            """
            status = 'prepared'
            OR (
                reference IS NOT NULL
                AND length(trim(reference)) > 0
            )
            """,
            name="ck_ticre_reference",
        ),
        Index(
            "ix_ticre_correction_date",
            "company_id",
            "tax_invoice_correction_id",
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

    tax_invoice_correction_id: Mapped[int] = mapped_column(
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
        nullable=False,
        default=lambda: datetime.now(
            timezone.utc
        ),
    )
