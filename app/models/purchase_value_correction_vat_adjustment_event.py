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


class PurchaseValueCorrectionVatAdjustmentEvent(Base):
    """
    Immutable economic INPUT VAT adjustment caused by one PURCHASE
    TradeValueCorrectionEvent.

    This is an economic VAT bridge fact, not legal evidence of a
    registered adjustment calculation (RK).

    Provenance:
        TradeValueCorrectionEvent
        +
        TaxCalculation

    Signed semantics are explicit through adjustment_kind:

        decrease
            supplier liability and economic INPUT VAT decrease
            Dr supplier payable / Cr VAT input bridge
            General 291: Dr 631 / Cr 644

        increase
            supplier liability and economic INPUT VAT increase
            Dr VAT input bridge / Cr supplier payable
            General 291: Dr 644 / Cr 631

    adjusted_taxable_base and adjusted_tax_amount are absolute positive
    magnitudes. Direction is carried only by adjustment_kind.

    History is append-only:
        original -> reversal -> replacement

    Legal INPUT VAT credit correction is a separate lifecycle.
    """

    __tablename__ = (
        "purchase_value_correction_vat_adjustment_events"
    )

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "id",
            name="uq_pvcvae_event_company_id_id",
        ),
        UniqueConstraint(
            "company_id",
            "id",
            "trade_value_correction_event_id",
            "tax_calculation_id",
            "adjustment_kind",
            name="uq_pvcvae_event_company_id_id_source",
        ),
        UniqueConstraint(
            "reversal_of_id",
            name="uq_pvcvae_event_reversal_of",
        ),
        ForeignKeyConstraint(
            (
                "company_id",
                "trade_value_correction_event_id",
            ),
            (
                "trade_value_correction_events.company_id",
                "trade_value_correction_events.id",
            ),
            name=(
                "fk_pvcvae_event_company_"
                "trade_value_correction"
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
                "fk_pvcvae_event_company_"
                "tax_calculation"
            ),
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            (
                "company_id",
                "reversal_of_id",
                "trade_value_correction_event_id",
                "tax_calculation_id",
                "adjustment_kind",
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
                (
                    "purchase_value_correction_vat_"
                    "adjustment_events."
                    "trade_value_correction_event_id"
                ),
                (
                    "purchase_value_correction_vat_"
                    "adjustment_events.tax_calculation_id"
                ),
                (
                    "purchase_value_correction_vat_"
                    "adjustment_events.adjustment_kind"
                ),
            ),
            name="fk_pvcvae_event_reversal_source",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ("company_id",),
            ("companies.id",),
            name="fk_pvcvae_event_company",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ("created_by",),
            ("users.id",),
            name="fk_pvcvae_event_created_by",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "adjustment_kind IN ('decrease', 'increase')",
            name="ck_pvcvae_event_adjustment_kind",
        ),
        CheckConstraint(
            "adjusted_taxable_base >= 0",
            name="ck_pvcvae_event_base_nonnegative",
        ),
        CheckConstraint(
            "adjusted_tax_amount >= 0",
            name="ck_pvcvae_event_tax_nonnegative",
        ),
        CheckConstraint(
            (
                "adjusted_taxable_base > 0 "
                "OR adjusted_tax_amount > 0"
            ),
            name="ck_pvcvae_event_nonzero",
        ),
        CheckConstraint(
            "char_length(currency_code) = 3",
            name="ck_pvcvae_event_currency_length",
        ),
        CheckConstraint(
            (
                "reversal_of_id IS NULL "
                "OR reversal_of_id <> id"
            ),
            name="ck_pvcvae_event_not_self_reversal",
        ),
        Index(
            "ix_pvcvae_event_source_history",
            "company_id",
            "trade_value_correction_event_id",
            "tax_calculation_id",
            "id",
        ),
        Index(
            "ix_pvcvae_event_tax_calculation",
            "company_id",
            "tax_calculation_id",
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

    trade_value_correction_event_id: Mapped[int] = (
        mapped_column(
            Integer,
            nullable=False,
        )
    )

    tax_calculation_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    adjustment_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )

    adjustment_kind: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
    )

    adjusted_taxable_base: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
    )

    adjusted_tax_amount: Mapped[Decimal] = mapped_column(
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
