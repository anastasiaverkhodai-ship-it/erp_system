from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class PurchaseValueCorrectionInputVatCreditCorrectionEvent(
    Base
):
    """
    Immutable legal buyer-side INPUT VAT credit correction caused by one
    PurchaseValueCorrectionVatAdjustmentEvent.

    correction_kind:

        decrease
            reduce previously recognized INPUT VAT credit
            Dr VAT input bridge / Cr tax settlement
            General 291: Dr 644 / Cr 641

        increase
            increase INPUT VAT credit
            Dr tax settlement / Cr VAT input bridge
            General 291: Dr 641 / Cr 644

    A decrease is not made dependent on registered RK evidence by this
    model. Reconciliation must cap it by credit that was actually
    recognized.

    An increase requires registered adjustment evidence. Therefore
    tax_credit_evidence_id is required for increase and forbidden for
    decrease.

    This model does not mutate TaxRecognitionEvent, TaxCreditEvidence,
    TaxCalculation, or the economic PVC VAT event.

    History is append-only:
        original -> reversal -> replacement
    """

    __tablename__ = (
        "purchase_value_correction_input_vat_credit_"
        "correction_events"
    )

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "id",
            name="uq_pvcivcc_event_company_id_id",
        ),
        UniqueConstraint(
            "company_id",
            "id",
            "purchase_value_correction_vat_adjustment_event_id",
            "tax_calculation_id",
            "correction_kind",
            name="uq_pvcivcc_event_company_id_id_source",
        ),
        UniqueConstraint(
            "reversal_of_id",
            name="uq_pvcivcc_event_reversal_of",
        ),
        ForeignKeyConstraint(
            ("company_id",),
            ("companies.id",),
            name="fk_pvcivcc_event_company",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ("created_by",),
            ("users.id",),
            name="fk_pvcivcc_event_created_by",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            (
                "company_id",
                "purchase_value_correction_vat_adjustment_event_id",
            ),
            (
                (
                    "purchase_value_correction_vat_"
                    "adjustment_events.company_id"
                ),
                (
                    "purchase_value_correction_vat_"
                    "adjustment_events.id"
                ),
            ),
            name=(
                "fk_pvcivcc_event_company_"
                "pvc_vat_adjustment"
            ),
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            (
                "company_id",
                "tax_calculation_id",
            ),
            (
                "tax_calculations.company_id",
                "tax_calculations.id",
            ),
            name=(
                "fk_pvcivcc_event_company_"
                "tax_calculation"
            ),
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            (
                "company_id",
                "tax_credit_evidence_id",
            ),
            (
                "tax_credit_evidence.company_id",
                "tax_credit_evidence.id",
            ),
            name=(
                "fk_pvcivcc_event_company_"
                "tax_credit_evidence"
            ),
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            (
                "company_id",
                "reversal_of_id",
                "purchase_value_correction_vat_adjustment_event_id",
                "tax_calculation_id",
                "correction_kind",
            ),
            (
                (
                    "purchase_value_correction_input_vat_"
                    "credit_correction_events.company_id"
                ),
                (
                    "purchase_value_correction_input_vat_"
                    "credit_correction_events.id"
                ),
                (
                    "purchase_value_correction_input_vat_"
                    "credit_correction_events."
                    "purchase_value_correction_vat_"
                    "adjustment_event_id"
                ),
                (
                    "purchase_value_correction_input_vat_"
                    "credit_correction_events."
                    "tax_calculation_id"
                ),
                (
                    "purchase_value_correction_input_vat_"
                    "credit_correction_events."
                    "correction_kind"
                ),
            ),
            name="fk_pvcivcc_event_reversal_source",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "correction_kind IN ('decrease', 'increase')",
            name="ck_pvcivcc_event_correction_kind",
        ),
        CheckConstraint(
            "corrected_taxable_base >= 0",
            name="ck_pvcivcc_event_base_nonnegative",
        ),
        CheckConstraint(
            "corrected_tax_amount >= 0",
            name="ck_pvcivcc_event_tax_nonnegative",
        ),
        CheckConstraint(
            (
                "corrected_taxable_base > 0 "
                "OR corrected_tax_amount > 0"
            ),
            name="ck_pvcivcc_event_nonzero",
        ),
        CheckConstraint(
            (
                "("
                "correction_kind = 'decrease' "
                "AND tax_credit_evidence_id IS NULL"
                ") OR ("
                "correction_kind = 'increase' "
                "AND tax_credit_evidence_id IS NOT NULL"
                ")"
            ),
            name="ck_pvcivcc_event_evidence_direction",
        ),
        CheckConstraint(
            "char_length(currency_code) = 3",
            name="ck_pvcivcc_event_currency_length",
        ),
        CheckConstraint(
            (
                "reversal_of_id IS NULL "
                "OR reversal_of_id <> id"
            ),
            name="ck_pvcivcc_event_not_self_reversal",
        ),
        Index(
            "ix_pvcivcc_event_source_history",
            "company_id",
            "purchase_value_correction_vat_adjustment_event_id",
            "tax_calculation_id",
            "id",
        ),
        Index(
            "ix_pvcivcc_event_tax_calculation",
            "company_id",
            "tax_calculation_id",
            "adjustment_date",
            "id",
        ),
        Index(
            "ix_pvcivcc_event_tax_credit_evidence",
            "company_id",
            "tax_credit_evidence_id",
            "id",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    company_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    purchase_value_correction_vat_adjustment_event_id: Mapped[
        int
    ] = mapped_column(
        Integer,
        nullable=False,
    )

    tax_calculation_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    tax_credit_evidence_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    adjustment_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )

    correction_kind: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
    )

    corrected_taxable_base: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
    )

    corrected_tax_amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
    )

    currency_code: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
    )

    created_by: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    reversal_of_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
