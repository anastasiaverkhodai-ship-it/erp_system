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
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class PurchaseReturnVatAdjustmentEvent(Base):
    """
    Immutable Ukrainian INPUT VAT adjustment fact for a Purchase Return.

    Provenance:
        PurchaseReturnRecognitionEvent
        +
        TaxCalculation

    PurchaseReturnRecognitionEvent proves the immutable economic return
    allocation. TaxCalculation identifies the immutable INPUT VAT
    calculation whose taxable base / tax are being adjusted.

    adjustment_date is an explicit tax-accounting business date. It is not
    inferred by this model from warehouse, recognition, or evidence dates.

    basis_kind records which legal business fact established that date:
        goods_received_by_supplier
        refund_by_supplier

    adjusted_taxable_base and adjusted_tax_amount are independent VAT
    snapshots. adjusted_taxable_base must not be substituted with the
    Purchase Return warehouse/accounting base.

    This event is only the immutable tax-domain source. Accounting posting,
    INPUT VAT recognition correction, and registered RK evidence belong to
    later lifecycles.

    History is append-only. Reversal is another immutable row.
    """

    __tablename__ = "purchase_return_vat_adjustment_events"

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "id",
            name="uq_prvae_event_company_id_id",
        ),
        UniqueConstraint(
            "company_id",
            "id",
            "purchase_return_recognition_event_id",
            "tax_calculation_id",
            "basis_kind",
            name="uq_prvae_event_company_id_id_source",
        ),
        UniqueConstraint(
            "reversal_of_id",
            name="uq_prvae_event_reversal_of",
        ),
        ForeignKeyConstraint(
            (
                "company_id",
                "purchase_return_recognition_event_id",
            ),
            (
                "purchase_return_recognition_events.company_id",
                "purchase_return_recognition_events.id",
            ),
            name=(
                "fk_prvae_event_company_"
                "purchase_return_recognition"
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
            name="fk_prvae_event_company_tax_calculation",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            (
                "company_id",
                "reversal_of_id",
                "purchase_return_recognition_event_id",
                "tax_calculation_id",
                "basis_kind",
            ),
            (
                "purchase_return_vat_adjustment_events.company_id",
                "purchase_return_vat_adjustment_events.id",
                (
                    "purchase_return_vat_adjustment_events."
                    "purchase_return_recognition_event_id"
                ),
                (
                    "purchase_return_vat_adjustment_events."
                    "tax_calculation_id"
                ),
                (
                    "purchase_return_vat_adjustment_events."
                    "basis_kind"
                ),
            ),
            name="fk_prvae_event_reversal_source",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            (
                "basis_kind IN ("
                "'goods_received_by_supplier', "
                "'refund_by_supplier'"
                ")"
            ),
            name="ck_prvae_event_basis_kind",
        ),
        CheckConstraint(
            "adjusted_taxable_base >= 0",
            name="ck_prvae_event_taxable_base_nonnegative",
        ),
        CheckConstraint(
            "adjusted_tax_amount >= 0",
            name="ck_prvae_event_tax_nonnegative",
        ),
        CheckConstraint(
            (
                "adjusted_taxable_base > 0 "
                "OR adjusted_tax_amount > 0"
            ),
            name="ck_prvae_event_nonzero_adjustment",
        ),
        CheckConstraint(
            "char_length(currency_code) = 3",
            name="ck_prvae_event_currency_length",
        ),
        CheckConstraint(
            (
                "reversal_of_id IS NULL "
                "OR reversal_of_id <> id"
            ),
            name="ck_prvae_event_not_self_reversal",
        ),
        Index(
            "ix_prvae_event_source_history",
            "company_id",
            "purchase_return_recognition_event_id",
            "tax_calculation_id",
            "id",
            unique=False,
        ),
        Index(
            "ix_prvae_event_tax_calculation",
            "company_id",
            "tax_calculation_id",
            "adjustment_date",
            "id",
            unique=False,
        ),
        Index(
            "ix_prvae_event_adjustment_date",
            "company_id",
            "adjustment_date",
            "id",
            unique=False,
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

    purchase_return_recognition_event_id: Mapped[int] = (
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

    basis_kind: Mapped[str] = mapped_column(
        String(32),
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

    reversal_of_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
