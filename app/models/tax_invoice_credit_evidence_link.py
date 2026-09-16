from sqlalchemy import (
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class TaxInvoiceCreditEvidenceLink(Base):
    """
    Immutable provenance link between one canonical INPUT tax invoice
    and one immutable TaxCreditEvidence row.

    A tax invoice may contain multiple evidence/calculation rows.
    One evidence row can belong to only one canonical tax invoice.
    """

    __tablename__ = "tax_invoice_credit_evidence_links"

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "id",
            name="uq_ticel_company_id",
        ),
        UniqueConstraint(
            "company_id",
            "tax_credit_evidence_id",
            name="uq_ticel_evidence",
        ),
        UniqueConstraint(
            "company_id",
            "tax_invoice_id",
            "tax_calculation_id",
            name="uq_ticel_invoice_calc",
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
            name="fk_ticel_invoice",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "company_id",
                "tax_credit_evidence_id",
                "tax_calculation_id",
            ],
            [
                "tax_credit_evidence.company_id",
                "tax_credit_evidence.id",
                "tax_credit_evidence.tax_calculation_id",
            ],
            name="fk_ticel_evidence",
            ondelete="RESTRICT",
        ),
        Index(
            "ix_ticel_invoice",
            "company_id",
            "tax_invoice_id",
        ),
        Index(
            "ix_ticel_evidence",
            "company_id",
            "tax_credit_evidence_id",
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

    tax_credit_evidence_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    tax_calculation_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
