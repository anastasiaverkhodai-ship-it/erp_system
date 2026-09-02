from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class TaxCreditEvidence(Base):
    """
    Persistent immutable legal-evidence ledger for INPUT VAT.

    A TaxCalculation with direction=INPUT is only a calculated
    purchase-tax snapshot. It does not itself prove that the
    company may recognize VAT tax credit.

    TaxCreditEvidence stores the separate durable evidence that
    may support that right.

    Evidence categories are defined by TaxCreditEvidenceType.

    credit_available_date is the earliest business date on which
    this evidence may be consumed by the future INPUT VAT
    recognition service. The evidence service, not this model,
    determines that date according to the evidence type and legal
    rules.

    effective_date is the immutable ledger-effective date.
    For original evidence it normally equals
    credit_available_date. For reversal evidence it is the
    date on which the original evidence capacity is removed.

    Amounts are positive capacities. A decrease/cancellation is
    represented through immutable reversal history rather than by
    mutating or storing negative evidence.

    reversal_of_id points to one original evidence row belonging
    to the same company and TaxCalculation.

    Recognition state is deliberately NOT stored here.
    Future INPUT TaxRecognitionEvent rows will consume evidence
    capacity while this ledger remains immutable.
    """

    __tablename__ = "tax_credit_evidence"

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "id",
            name=(
                "uq_tax_credit_evidence_"
                "company_id_id"
            ),
        ),
        UniqueConstraint(
            "company_id",
            "id",
            "tax_calculation_id",
            name=(
                "uq_tax_credit_evidence_"
                "company_id_id_tax_calculation"
            ),
        ),
        UniqueConstraint(
            "reversal_of_id",
            name=(
                "uq_tax_credit_evidence_"
                "reversal_of"
            ),
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
            name=(
                "fk_tax_credit_evidence_"
                "company_tax_calculation"
            ),
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "company_id",
                "reversal_of_id",
                "tax_calculation_id",
            ],
            [
                "tax_credit_evidence.company_id",
                "tax_credit_evidence.id",
                "tax_credit_evidence.tax_calculation_id",
            ],
            name=(
                "fk_tax_credit_evidence_"
                "company_reversal_tax_calculation"
            ),
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            (
                "evidence_type IN ("
                "'registered_tax_invoice', "
                "'registered_adjustment', "
                "'customs_declaration', "
                "'article_201_11_document', "
                "'nonresident_self_invoice'"
                ")"
            ),
            name=(
                "ck_tax_credit_evidence_"
                "evidence_type"
            ),
        ),
        CheckConstraint(
            (
                "char_length(trim(evidence_number)) "
                "> 0"
            ),
            name=(
                "ck_tax_credit_evidence_"
                "evidence_number_nonempty"
            ),
        ),
        CheckConstraint(
            "evidenced_taxable_base >= 0",
            name=(
                "ck_tax_credit_evidence_"
                "taxable_base_nonnegative"
            ),
        ),
        CheckConstraint(
            "evidenced_tax_amount > 0",
            name=(
                "ck_tax_credit_evidence_"
                "tax_amount_positive"
            ),
        ),
        CheckConstraint(
            "char_length(currency_code) = 3",
            name=(
                "ck_tax_credit_evidence_"
                "currency_code_length"
            ),
        ),
        CheckConstraint(
            (
                "reversal_of_id IS NULL "
                "OR reversal_of_id <> id"
            ),
            name=(
                "ck_tax_credit_evidence_"
                "not_self_reversal"
            ),
        ),
        Index(
            (
                "ix_tax_credit_evidence_"
                "source_original"
            ),
            "company_id",
            "tax_calculation_id",
            "evidence_type",
            "evidence_number",
            "id",
            unique=False,
            postgresql_where=text(
                "reversal_of_id IS NULL"
            ),
        ),
    )

    id: Mapped[int] = mapped_column(
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

    tax_calculation_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    evidence_type: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
    )

    evidence_number: Mapped[str] = mapped_column(
        String(120),
        nullable=False,
    )

    evidence_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
        index=True,
    )

    credit_available_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
        index=True,
    )

    effective_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
        index=True,
    )

    evidenced_taxable_base: Mapped[
        Decimal
    ] = mapped_column(
        Numeric(
            precision=18,
            scale=2,
        ),
        nullable=False,
    )

    evidenced_tax_amount: Mapped[
        Decimal
    ] = mapped_column(
        Numeric(
            precision=18,
            scale=2,
        ),
        nullable=False,
    )

    currency_code: Mapped[str] = mapped_column(
        String(3),
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
        DateTime(
            timezone=True,
        ),
        server_default=func.now(),
        nullable=False,
    )

    reversal_of_id: Mapped[
        int | None
    ] = mapped_column(
        Integer,
        nullable=True,
        index=True,
    )
