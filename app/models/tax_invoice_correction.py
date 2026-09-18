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


class TaxInvoiceCorrection(Base):
    """
    Canonical legal VAT correction document (РК).

    This model is deliberately separate from:
      * operational return/value-correction events;
      * VAT adjustment events;
      * TaxRecognitionEvent;
      * TaxCreditEvidence.

    The header is an immutable legal snapshot linked to exactly one
    canonical original TaxInvoice. Registration state is not stored
    here and is derived from append-only registration history.
    """

    __tablename__ = "tax_invoice_corrections"

    registration_party: Mapped[str] = mapped_column(String(6), nullable=False, default='seller', server_default='seller')

    __table_args__ = (
        CheckConstraint("registration_party IN ('seller','buyer')",name='ck_tic_registration_party'),
        UniqueConstraint(
            "company_id",
            "id",
            name="uq_tic_company_id",
        ),
        ForeignKeyConstraint(
            [
                "company_id",
                "original_tax_invoice_id",
            ],
            [
                "tax_invoices.company_id",
                "tax_invoices.id",
            ],
            name="fk_tic_original_invoice",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "direction IN ('input', 'output')",
            name="ck_tic_direction",
        ),
        CheckConstraint(
            "currency_code = 'UAH'",
            name="ck_tic_uah",
        ),
        CheckConstraint(
            "length(trim(document_number)) > 0",
            name="ck_tic_number",
        ),
        CheckConstraint(
            "length(trim(seller_name)) > 0",
            name="ck_tic_seller_name",
        ),
        CheckConstraint(
            "length(trim(seller_tax_number)) > 0",
            name="ck_tic_seller_tax",
        ),
        CheckConstraint(
            "length(trim(seller_vat_number)) > 0",
            name="ck_tic_seller_vat",
        ),
        CheckConstraint(
            """
            (
                buyer_name IS NULL
                AND buyer_tax_number IS NULL
                AND buyer_vat_number IS NULL
            )
            OR
            (
                length(trim(buyer_name)) > 0
                AND length(trim(buyer_tax_number)) > 0
                AND length(trim(buyer_vat_number)) > 0
            )
            """,
            name="ck_tic_buyer_shape",
        ),
        Index(
            "ix_tic_company_date",
            "company_id",
            "document_date",
            "id",
        ),
        Index(
            "ix_tic_original_invoice",
            "company_id",
            "original_tax_invoice_id",
            "id",
        ),
        Index(
            "ix_tic_number",
            "company_id",
            "document_number",
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

    original_tax_invoice_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    direction: Mapped[str] = mapped_column(
        String(6),
        nullable=False,
    )

    document_number: Mapped[str] = mapped_column(
        String(120),
        nullable=False,
    )

    document_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )

    currency_code: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
        default="UAH",
    )

    seller_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    seller_tax_number: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )

    seller_vat_number: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )

    buyer_name: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    buyer_tax_number: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
    )

    buyer_vat_number: Mapped[str | None] = mapped_column(
        String(20),
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
