from datetime import (
    date,
    datetime,
)
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
from sqlalchemy.orm import (
    Mapped,
    mapped_column,
)

from app.core.database import Base


class PurchaseReturnInputVatCreditCorrectionEvent(
    Base
):
    """
    Immutable buyer-side legal INPUT VAT credit correction caused
    by one PurchaseReturnVatAdjustmentEvent.

    This event is deliberately separate from:

    - PurchaseReturnVatAdjustmentEvent:
      economic VAT liability correction, Dr 631 / Cr 644;

    - TaxRecognitionEvent:
      positive legal INPUT VAT recognition from TaxCreditEvidence,
      Dr 641 / Cr 644.

    A positive original row represents the amount of INPUT VAT
    credit that must be reduced in the legal adjustment period.

    Future accounting milestone:
        Dr VAT_INPUT
        Cr TAX_SETTLEMENT

        GENERAL 291:
        Dr 644
        Cr 641

    A reversal copies source identity and historical amounts and
    later restores that legal correction through its own immutable
    reversal lifecycle.

    Zero desired correction is represented by no positive original
    row or by reversal-only reconciliation of existing history.
    Zero-zero rows are never persisted.

    reduced_taxable_base and reduced_tax_amount are independent.
    Neither is derived from the other.

    No TaxCreditEvidence is created or mutated.
    No TaxRecognitionEvent is mutated.
    No TaxCalculation is mutated.
    """

    __tablename__ = (
        "purchase_return_input_vat_credit_correction_events"
    )

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "id",
            name=(
                "uq_privcc_event_"
                "company_id_id"
            ),
        ),
        UniqueConstraint(
            "company_id",
            "id",
            "purchase_return_vat_adjustment_event_id",
            "tax_calculation_id",
            name=(
                "uq_privcc_event_"
                "company_id_id_source"
            ),
        ),
        UniqueConstraint(
            "reversal_of_id",
            name=(
                "uq_privcc_event_"
                "reversal_of"
            ),
        ),
        ForeignKeyConstraint(
            [
                "company_id",
            ],
            [
                "companies.id",
            ],
            name=(
                "fk_privcc_event_company"
            ),
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "created_by",
            ],
            [
                "users.id",
            ],
            name=(
                "fk_privcc_event_created_by"
            ),
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "company_id",
                "purchase_return_vat_adjustment_event_id",
            ],
            [
                "purchase_return_vat_adjustment_events.company_id",
                "purchase_return_vat_adjustment_events.id",
            ],
            name=(
                "fk_privcc_event_company_"
                "purchase_return_vat_adjustment"
            ),
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
            name=(
                "fk_privcc_event_company_"
                "tax_calculation"
            ),
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "company_id",
                "reversal_of_id",
                "purchase_return_vat_adjustment_event_id",
                "tax_calculation_id",
            ],
            [
                (
                    "purchase_return_input_vat_credit_"
                    "correction_events.company_id"
                ),
                (
                    "purchase_return_input_vat_credit_"
                    "correction_events.id"
                ),
                (
                    "purchase_return_input_vat_credit_"
                    "correction_events."
                    "purchase_return_vat_adjustment_event_id"
                ),
                (
                    "purchase_return_input_vat_credit_"
                    "correction_events.tax_calculation_id"
                ),
            ],
            name=(
                "fk_privcc_event_"
                "reversal_source"
            ),
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "reduced_taxable_base >= 0",
            name=(
                "ck_privcc_event_"
                "taxable_base_nonnegative"
            ),
        ),
        CheckConstraint(
            "reduced_tax_amount >= 0",
            name=(
                "ck_privcc_event_"
                "tax_nonnegative"
            ),
        ),
        CheckConstraint(
            "reduced_taxable_base > 0 "
            "OR reduced_tax_amount > 0",
            name=(
                "ck_privcc_event_"
                "nonzero_correction"
            ),
        ),
        CheckConstraint(
            "char_length(currency_code) = 3",
            name=(
                "ck_privcc_event_"
                "currency_length"
            ),
        ),
        CheckConstraint(
            "reversal_of_id IS NULL "
            "OR reversal_of_id <> id",
            name=(
                "ck_privcc_event_"
                "not_self_reversal"
            ),
        ),
        Index(
            "ix_privcc_event_source_history",
            "company_id",
            "purchase_return_vat_adjustment_event_id",
            "tax_calculation_id",
            "id",
        ),
        Index(
            "ix_privcc_event_tax_calculation",
            "company_id",
            "tax_calculation_id",
            "adjustment_date",
            "id",
        ),
        Index(
            "ix_privcc_event_adjustment_date",
            "company_id",
            "adjustment_date",
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

    purchase_return_vat_adjustment_event_id: Mapped[
        int
    ] = mapped_column(
        Integer,
        nullable=False,
    )

    tax_calculation_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    adjustment_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )

    reduced_taxable_base: Mapped[
        Decimal
    ] = mapped_column(
        Numeric(
            18,
            2,
        ),
        nullable=False,
    )

    reduced_tax_amount: Mapped[
        Decimal
    ] = mapped_column(
        Numeric(
            18,
            2,
        ),
        nullable=False,
    )

    currency_code: Mapped[str] = mapped_column(
        String(
            3
        ),
        nullable=False,
    )

    created_by: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    created_at: Mapped[
        datetime
    ] = mapped_column(
        DateTime(
            timezone=True
        ),
        nullable=False,
        server_default=func.now(),
    )

    reversal_of_id: Mapped[
        int | None
    ] = mapped_column(
        Integer,
        nullable=True,
    )
