from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ProductTaxClassification(Base):
    """
    Dated statutory classification used when producing Ukrainian
    tax-invoice line snapshots.

    The master is append-only by effective_from. TaxInvoiceLine copies
    classification_kind and statutory_code into its immutable snapshot,
    so historical tax invoices never depend on later master-data edits.
    """

    __tablename__ = "product_tax_classifications"

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "id",
            name="uq_ptc_company_id",
        ),
        UniqueConstraint(
            "company_id",
            "product_id",
            "effective_from",
            name="uq_ptc_product_date",
        ),
        CheckConstraint(
            "classification_kind IN ('uktzed', 'dkpp')",
            name="ck_ptc_kind",
        ),
        CheckConstraint(
            "length(trim(statutory_code)) > 0",
            name="ck_ptc_code_nonempty",
        ),
        Index(
            "ix_ptc_product_date",
            "company_id",
            "product_id",
            "effective_from",
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

    product_id: Mapped[int] = mapped_column(
        ForeignKey(
            "products.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )

    effective_from: Mapped[date] = mapped_column(
        Date,
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
