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
    text,
)
from sqlalchemy.orm import (
    Mapped,
    mapped_column,
)

from app.core.database import Base


class SupplierAdvanceClearingEvent(Base):
    """
    Immutable accounting event representing supplier-advance
    clearing against economic supplier liability.

    One positive original represents:

        Dr SUPPLIER_PAYABLES
        Cr SUPPLIER_ADVANCES

    GENERAL 291:

        Dr 631
        Cr 371

    Provenance is the intersection of:

    * one ACTIVE commercial PaymentSettlementAllocation;
    * one ACTIVE purchase InvoiceFulfillmentAllocation.

    The accounting date is determined by reconciliation as the
    later of commercial settlement date and economic liability date.

    Historical events are never updated or deleted. Removing or
    replacing desired state creates an immutable reversal event
    through reversal_of_id.
    """

    __tablename__ = (
        "supplier_advance_clearing_events"
    )

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "id",
            name="uq_sac_event_company_id_id",
        ),
        UniqueConstraint(
            "company_id",
            "id",
            "payment_settlement_allocation_id",
            "invoice_fulfillment_allocation_id",
            name=(
                "uq_sac_event_"
                "company_id_id_source"
            ),
        ),
        UniqueConstraint(
            "reversal_of_id",
            name="uq_sac_event_reversal_of",
        ),
        ForeignKeyConstraint(
            (
                "company_id",
                "payment_settlement_allocation_id",
            ),
            (
                "payment_settlement_allocations.company_id",
                "payment_settlement_allocations.id",
            ),
            name=(
                "fk_sac_event_"
                "company_settlement"
            ),
        ),
        ForeignKeyConstraint(
            (
                "company_id",
                "invoice_fulfillment_allocation_id",
            ),
            (
                "invoice_fulfillment_allocations.company_id",
                "invoice_fulfillment_allocations.id",
            ),
            name=(
                "fk_sac_event_"
                "company_liability"
            ),
        ),
        ForeignKeyConstraint(
            (
                "company_id",
                "reversal_of_id",
                "payment_settlement_allocation_id",
                "invoice_fulfillment_allocation_id",
            ),
            (
                "supplier_advance_clearing_events.company_id",
                "supplier_advance_clearing_events.id",
                (
                    "supplier_advance_clearing_events."
                    "payment_settlement_allocation_id"
                ),
                (
                    "supplier_advance_clearing_events."
                    "invoice_fulfillment_allocation_id"
                ),
            ),
            name=(
                "fk_sac_event_"
                "reversal_source"
            ),
        ),
        CheckConstraint(
            "cleared_amount > 0",
            name="ck_sac_event_amount_positive",
        ),
        CheckConstraint(
            "char_length(currency_code) = 3",
            name="ck_sac_event_currency_len",
        ),
        CheckConstraint(
            (
                "reversal_of_id IS NULL "
                "OR reversal_of_id <> id"
            ),
            name=(
                "ck_sac_event_"
                "not_self_reversal"
            ),
        ),
        Index(
            "ix_sac_event_company",
            "company_id",
        ),
        Index(
            "ix_sac_event_settlement",
            "payment_settlement_allocation_id",
        ),
        Index(
            "ix_sac_event_liability",
            "invoice_fulfillment_allocation_id",
        ),
        Index(
            "ix_sac_event_date",
            "clearing_date",
        ),
        Index(
            "ix_sac_event_source_original",
            "company_id",
            "payment_settlement_allocation_id",
            "invoice_fulfillment_allocation_id",
            "id",
            unique=False,
            postgresql_where=text(
                "reversal_of_id IS NULL"
            ),
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

    payment_settlement_allocation_id: Mapped[
        int
    ] = mapped_column(
        Integer,
        nullable=False,
    )

    invoice_fulfillment_allocation_id: Mapped[
        int
    ] = mapped_column(
        Integer,
        nullable=False,
    )

    clearing_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )

    cleared_amount: Mapped[Decimal] = mapped_column(
        Numeric(
            precision=18,
            scale=2,
        ),
        nullable=False,
    )

    currency_code: Mapped[str] = mapped_column(
        String(
            length=3,
        ),
        nullable=False,
    )

    created_by: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(
            timezone=True,
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
