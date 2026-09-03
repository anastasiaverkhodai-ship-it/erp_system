from datetime import (
    date,
    datetime,
)
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
from sqlalchemy.orm import (
    Mapped,
    mapped_column,
)

from app.core.database import Base


class InputVatFulfillmentBridgeEvent(Base):
    """
    Immutable economic INPUT VAT fulfillment bridge event.

    This ledger is separate from legal tax-credit recognition.

    Source identity:

        tax_calculation_id
        +
        invoice_fulfillment_allocation_id

    One original source event represents the INPUT VAT amount
    economically contained in one ACTIVE purchase fulfillment
    allocation:

        Dr VAT_INPUT
        Cr SUPPLIER_PAYABLES

        GENERAL 291:
        Dr 644
        Cr 631

    Later qualifying TaxCreditEvidence is represented separately by
    INPUT TaxRecognitionEvent:

        Dr TAX_SETTLEMENT
        Cr VAT_INPUT

        GENERAL 291:
        Dr 641
        Cr 644

    Allocation reversal never mutates or deletes bridge history.
    It creates one immutable reversal event through reversal_of_id.

    InvoiceFulfillmentAllocation and TaxCalculation remain immutable
    provenance. Normal reconciliation keeps at most one ACTIVE
    original for one source. If aggregate VAT rounding/capping changes
    the desired amount, the active original is reversed and one full
    replacement original may be created. Historical rows are never
    mutated or deleted.
    """

    __tablename__ = (
        "input_vat_fulfillment_bridge_events"
    )

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "id",
            name=(
                'uq_ivfb_event_company_id_id'
            ),
        ),
        UniqueConstraint(
            "company_id",
            "id",
            "tax_calculation_id",
            "invoice_fulfillment_allocation_id",
            name=(
                'uq_ivfb_event_company_id_id_source'
            ),
        ),
        UniqueConstraint(
            "reversal_of_id",
            name=(
                'uq_ivfb_event_reversal_of'
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
                'fk_ivfb_event_company_tax_calc'
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
                'fk_ivfb_event_company_allocation'
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
                (
                    "input_vat_fulfillment_bridge_events."
                    "company_id"
                ),
                (
                    "input_vat_fulfillment_bridge_events."
                    "id"
                ),
                (
                    "input_vat_fulfillment_bridge_events."
                    "tax_calculation_id"
                ),
                (
                    "input_vat_fulfillment_bridge_events."
                    "invoice_fulfillment_allocation_id"
                ),
            ],
            name=(
                'fk_ivfb_event_reversal_source'
            ),
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "bridged_tax_amount > 0",
            name=(
                'ck_ivfb_event_tax_amount_positive'
            ),
        ),
        CheckConstraint(
            "char_length(currency_code) = 3",
            name=(
                'ck_ivfb_event_currency_len'
            ),
        ),
        CheckConstraint(
            (
                "reversal_of_id IS NULL "
                "OR reversal_of_id <> id"
            ),
            name=(
                'ck_ivfb_event_not_self_reversal'
            ),
        ),
        Index(
            "ix_ivfb_event_company",
            "company_id",
        ),
        Index(
            "ix_ivfb_event_tax_calc",
            "tax_calculation_id",
        ),
        Index(
            "ix_ivfb_event_allocation",
            "invoice_fulfillment_allocation_id",
        ),
        Index(
            "ix_ivfb_event_bridge_date",
            "bridge_date",
        ),
        Index(
            (
                'ix_ivfb_event_source_original'
            ),
            "company_id",
            "tax_calculation_id",
            "invoice_fulfillment_allocation_id",
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
    )

    tax_calculation_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    invoice_fulfillment_allocation_id: Mapped[
        int
    ] = mapped_column(
        Integer,
        nullable=False,
    )

    bridge_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
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
    )
