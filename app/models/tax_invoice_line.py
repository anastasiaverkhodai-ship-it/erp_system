from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class TaxInvoiceLine(Base):
    """
    Immutable statutory line snapshot.

    Values required for the legal document are copied here rather than
    read later from mutable Product / TradeDocumentLine master data.

    tax_calculation_id preserves VAT-calculation provenance.
    tax_recognition_event_id preserves exact OUTPUT first-event
    provenance when applicable.
    """

    __tablename__ = "tax_invoice_lines"

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "id",
            name="uq_til_company_id",
        ),
        UniqueConstraint(
            "company_id",
            "tax_invoice_id",
            "line_number",
            name="uq_til_invoice_line",
        ),
        UniqueConstraint(
            "company_id",
            "tax_invoice_id",
            "tax_calculation_id",
            name="uq_til_invoice_calc",
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
            name="fk_til_invoice",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "company_id",
                "tax_calculation_id",
            ],
            [
                "tax_calculations.company_id",
                "tax_calculations.id",
            ],
            name="fk_til_calculation",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "company_id",
                "tax_recognition_event_id",
                "tax_calculation_id",
            ],
            [
                "tax_recognition_events.company_id",
                "tax_recognition_events.id",
                "tax_recognition_events.tax_calculation_id",
            ],
            name="fk_til_recognition",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "line_number > 0",
            name="ck_til_line_positive",
        ),
        CheckConstraint(
            "length(trim(description)) > 0",
            name="ck_til_description",
        ),
        CheckConstraint(
            "quantity > 0",
            name="ck_til_quantity",
        ),
        CheckConstraint(
            "length(trim(uom_code)) > 0",
            name="ck_til_uom",
        ),
        CheckConstraint(
            "classification_kind IN ('uktzed', 'dkpp')",
            name="ck_til_classification",
        ),
        CheckConstraint(
            "length(trim(statutory_code)) > 0",
            name="ck_til_statutory_code",
        ),
        CheckConstraint(
            "unit_price_without_vat >= 0",
            name="ck_til_price",
        ),
        CheckConstraint(
            "taxable_base >= 0",
            name="ck_til_base",
        ),
        CheckConstraint(
            "tax_rate >= 0 AND tax_rate <= 1",
            name="ck_til_rate",
        ),
        CheckConstraint(
            "length(trim(tax_rate_code)) > 0",
            name="ck_til_rate_code",
        ),
        CheckConstraint(
            "tax_amount >= 0",
            name="ck_til_tax",
        ),
        CheckConstraint(
            "total_with_vat >= 0",
            name="ck_til_total",
        ),
        CheckConstraint(
            "total_with_vat = taxable_base + tax_amount",
            name="ck_til_total_math",
        ),
        Index(
            "ix_til_invoice",
            "company_id",
            "tax_invoice_id",
        ),
        Index(
            "ix_til_calculation",
            "company_id",
            "tax_calculation_id",
        ),
        Index(
            "ix_til_recognition",
            "company_id",
            "tax_recognition_event_id",
        ),
        Index(
            "ix_til_product",
            "company_id",
            "product_id",
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

    line_number: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    tax_calculation_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    tax_recognition_event_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    product_id: Mapped[int] = mapped_column(
        ForeignKey(
            "products.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )

    description: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
    )

    quantity: Mapped[Decimal] = mapped_column(
        Numeric(18, 6),
        nullable=False,
    )

    uom_code: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )

    classification_kind: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
    )

    statutory_code: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )

    unit_price_without_vat: Mapped[Decimal] = mapped_column(
        Numeric(18, 6),
        nullable=False,
    )

    taxable_base: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
    )

    tax_rate_code: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    tax_rate: Mapped[Decimal] = mapped_column(
        Numeric(9, 6),
        nullable=False,
    )

    tax_amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
    )

    total_with_vat: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
    )
