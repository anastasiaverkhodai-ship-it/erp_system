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


class TaxRecognitionEvent(Base):
    """
    Persistent immutable tax-recognition ledger event.

    tax_calculation_id identifies the calculated tax
    obligation / credit being recognized.

    Durable recognition sources currently supported:

    - invoice_fulfillment_allocation_id:
      line-level commercial matching to an actual persistent
      fulfillment / warehouse movement.

    - payment_settlement_allocation_id:
      payment amount durably allocated to an AR/AP obligation.

    - tax_credit_evidence_id:
        immutable legal evidence supporting INPUT VAT
        tax-credit recognition.

    At most one typed recognition source may be populated.

    A source-less event is reserved for explicit MANUAL
    recognition controlled by a future authorized service.

    Reversal is represented by another immutable event through
    reversal_of_id. A reversal must belong to the same company
    and the same TaxCalculation as the original event.

    Recognition balances are derived from immutable events.
    No mutable recognized / remaining balance is stored here.
    """

    __tablename__ = "tax_recognition_events"

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "id",
            name=(
                "uq_tax_recognition_events_"
                "company_id_id"
            ),
        ),
        UniqueConstraint(
            "company_id",
            "id",
            "tax_calculation_id",
            name=(
                "uq_tax_recognition_events_"
                "company_id_id_tax_calculation"
            ),
        ),
        UniqueConstraint(
            "reversal_of_id",
            name=(
                "uq_tax_recognition_events_"
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
                "fk_tax_recognition_events_"
                "company_tax_calculation"
            ),
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "company_id",
                "invoice_fulfillment_allocation_id",
            ],
            [
                (
                    "invoice_fulfillment_allocations."
                    "company_id"
                ),
                (
                    "invoice_fulfillment_allocations."
                    "id"
                ),
            ],
            name=(
                "fk_tax_recognition_events_"
                "company_invoice_fulfillment_alloc"
            ),
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "company_id",
                "payment_settlement_allocation_id",
            ],
            [
                (
                    "payment_settlement_allocations."
                    "company_id"
                ),
                (
                    "payment_settlement_allocations."
                    "id"
                ),
            ],
            name=(
                "fk_tax_recognition_events_"
                "company_payment_settlement_allocation"
            ),
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
            name=(
                "fk_tax_recognition_events_"
                "company_tax_credit_evidence"
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
                "tax_recognition_events.company_id",
                "tax_recognition_events.id",
                (
                    "tax_recognition_events."
                    "tax_calculation_id"
                ),
            ],
            name=(
                "fk_tax_recognition_events_"
                "company_reversal_of_tax_calculation"
            ),
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            """
            (
                invoice_fulfillment_allocation_id IS NULL
                OR payment_settlement_allocation_id IS NULL
            )
            AND (
                invoice_fulfillment_allocation_id IS NULL
                OR tax_credit_evidence_id IS NULL
            )
            AND (
                payment_settlement_allocation_id IS NULL
                OR tax_credit_evidence_id IS NULL
            )
            """,
            name=(
                "ck_tax_recognition_events_"
                "at_most_one_recognition_source"
            ),
        ),
        CheckConstraint(
            "recognized_taxable_base >= 0",
            name=(
                "ck_tax_recognition_events_"
                "taxable_base_nonnegative"
            ),
        ),
        CheckConstraint(
            "recognized_tax_amount >= 0",
            name=(
                "ck_tax_recognition_events_"
                "tax_amount_nonnegative"
            ),
        ),
        CheckConstraint(
            (
                "recognized_taxable_base > 0 "
                "OR recognized_tax_amount > 0"
            ),
            name=(
                "ck_tax_recognition_events_"
                "nonzero_recognition"
            ),
        ),
        CheckConstraint(
            "char_length(currency_code) = 3",
            name=(
                "ck_tax_recognition_events_"
                "currency_code_length"
            ),
        ),
        CheckConstraint(
            (
                "reversal_of_id IS NULL "
                "OR reversal_of_id <> id"
            ),
            name=(
                "ck_tax_recognition_events_"
                "not_self_reversal"
            ),
        ),
        Index(
            (
                "ix_tax_recognition_events_"
                "fulfillment_source"
            ),
            "company_id",
            "tax_calculation_id",
            "invoice_fulfillment_allocation_id",
            unique=False,
            postgresql_where=text(
                "reversal_of_id IS NULL "
                "AND invoice_fulfillment_allocation_id "
                "IS NOT NULL"
            ),
        ),
        Index(
            (
                "ix_tax_recognition_events_"
                "settlement_source"
            ),
            "company_id",
            "tax_calculation_id",
            "payment_settlement_allocation_id",
            unique=False,
            postgresql_where=text(
                "reversal_of_id IS NULL "
                "AND payment_settlement_allocation_id "
                "IS NOT NULL"
            ),
        ),
        Index(
            (
                "ix_tax_recognition_events_"
                "tax_credit_evidence_source"
            ),
            "company_id",
            "tax_calculation_id",
            "tax_credit_evidence_id",
            unique=False,
            postgresql_where=text(
                "reversal_of_id IS NULL "
                "AND tax_credit_evidence_id IS NOT NULL"
            ),
        ),
        Index(
            (
                "ix_tax_recognition_events_"
                "tax_calculation_original"
            ),
            "company_id",
            "tax_calculation_id",
            "id",
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

    invoice_fulfillment_allocation_id: Mapped[
        int | None
    ] = mapped_column(
        Integer,
        nullable=True,
        index=True,
    )

    payment_settlement_allocation_id: Mapped[
        int | None
    ] = mapped_column(
        Integer,
        nullable=True,
        index=True,
    )

    tax_credit_evidence_id: Mapped[
        int | None
    ] = mapped_column(
        Integer,
        nullable=True,
        index=True,
    )

    recognition_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
        index=True,
    )

    recognized_taxable_base: Mapped[
        Decimal
    ] = mapped_column(
        Numeric(
            precision=18,
            scale=2,
        ),
        nullable=False,
    )

    recognized_tax_amount: Mapped[
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
            timezone=True
        ),
        server_default=func.now(),
        nullable=False,
    )

    reversal_of_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        index=True,
    )
