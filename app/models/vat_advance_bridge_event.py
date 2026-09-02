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


class VatAdvanceBridgeEvent(Base):
    """
    Persistent immutable Sales/VAT advance bridge ledger event.

    The bridge exists only for the VAT amount already recognized from
    settlement/prepayment that is economically contained in a later
    SalesRecognitionEvent.

    Source identity:
        tax_calculation_id
        +
        invoice_fulfillment_allocation_id

    Positive original event:
        represents VAT that must be moved out of gross Sales revenue:

            Dr GOODS_REVENUE
            Cr VAT_OUTPUT

            Dr 702
            Cr 643

    Reversal is represented by another immutable event through
    reversal_of_id.

    Normal lifecycle keeps at most one ACTIVE original event for one
    source. If the desired bridge amount changes, the active original
    is reversed and one full replacement is created.

    Historical rows are never mutated or deleted by reconciliation.
    """

    __tablename__ = "vat_advance_bridge_events"

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "id",
            name=(
                "uq_vat_advance_bridge_events_"
                "company_id_id"
            ),
        ),
        UniqueConstraint(
            "company_id",
            "id",
            "tax_calculation_id",
            "invoice_fulfillment_allocation_id",
            name=(
                "uq_vat_advance_bridge_events_"
                "company_id_id_source"
            ),
        ),
        UniqueConstraint(
            "reversal_of_id",
            name=(
                "uq_vat_advance_bridge_events_"
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
                "fk_vat_advance_bridge_events_"
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
                "fk_vat_advance_bridge_events_"
                "company_fulfillment_source"
            ),
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "company_id",
                "reversal_of_id",
                "tax_calculation_id",
                "invoice_fulfillment_allocation_id",
            ],
            [
                "vat_advance_bridge_events.company_id",
                "vat_advance_bridge_events.id",
                (
                    "vat_advance_bridge_events."
                    "tax_calculation_id"
                ),
                (
                    "vat_advance_bridge_events."
                    "invoice_fulfillment_allocation_id"
                ),
            ],
            name=(
                "fk_vat_advance_bridge_events_"
                "company_reversal_of_source"
            ),
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "bridged_tax_amount > 0",
            name=(
                "ck_vat_advance_bridge_events_"
                "tax_amount_positive"
            ),
        ),
        CheckConstraint(
            "char_length(currency_code) = 3",
            name=(
                "ck_vat_advance_bridge_events_"
                "currency_code_length"
            ),
        ),
        CheckConstraint(
            (
                "reversal_of_id IS NULL "
                "OR reversal_of_id <> id"
            ),
            name=(
                "ck_vat_advance_bridge_events_"
                "not_self_reversal"
            ),
        ),
        Index(
            (
                "ix_vat_advance_bridge_events_"
                "source_original"
            ),
            "company_id",
            "tax_calculation_id",
            "invoice_fulfillment_allocation_id",
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

    invoice_fulfillment_allocation_id: Mapped[
        int
    ] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    bridge_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
        index=True,
    )

    bridged_tax_amount: Mapped[
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
