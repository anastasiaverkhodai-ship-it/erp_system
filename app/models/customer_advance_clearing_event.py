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


class CustomerAdvanceClearingEvent(Base):
    """
    Immutable accounting event representing customer-advance
    clearing against economic customer receivable.

    One positive original represents:

        Dr CUSTOMER_ADVANCES
        Cr CUSTOMER_RECEIVABLES

    GENERAL 291:

        Dr 681
        Cr 361

    Provenance is the intersection of:

    * one ACTIVE commercial PaymentSettlementAllocation;
    * one ACTIVE purchase SalesRecognitionEvent.

    The accounting date is determined by reconciliation as the
    later of commercial settlement date and economic liability date.

    Historical events are never updated or deleted. Removing or
    replacing desired state creates an immutable reversal event
    through reversal_of_id.
    """

    __tablename__ = (
        "customer_advance_clearing_events"
    )

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "id",
            name="uq_cac_event_company_id_id",
        ),
        UniqueConstraint(
            "company_id",
            "id",
            "payment_settlement_allocation_id",
            "sales_recognition_event_id",
            name=(
                "uq_cac_event_"
                "company_id_id_source"
            ),
        ),
        UniqueConstraint(
            "reversal_of_id",
            name="uq_cac_event_reversal_of",
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
                "fk_cac_event_"
                "company_settlement"
            ),
        ),
        ForeignKeyConstraint(
            (
                "company_id",
                "sales_recognition_event_id",
            ),
            (
                "sales_recognition_events.company_id",
                "sales_recognition_events.id",
            ),
            name=(
                "fk_cac_event_"
                "company_liability"
            ),
        ),
        ForeignKeyConstraint(
            (
                "company_id",
                "reversal_of_id",
                "payment_settlement_allocation_id",
                "sales_recognition_event_id",
            ),
            (
                "customer_advance_clearing_events.company_id",
                "customer_advance_clearing_events.id",
                (
                    "customer_advance_clearing_events."
                    "payment_settlement_allocation_id"
                ),
                (
                    "customer_advance_clearing_events."
                    "sales_recognition_event_id"
                ),
            ),
            name=(
                "fk_cac_event_"
                "reversal_source"
            ),
        ),
        CheckConstraint(
            "cleared_amount > 0",
            name="ck_cac_event_amount_positive",
        ),
        CheckConstraint(
            "char_length(currency_code) = 3",
            name="ck_cac_event_currency_len",
        ),
        CheckConstraint(
            (
                "reversal_of_id IS NULL "
                "OR reversal_of_id <> id"
            ),
            name=(
                "ck_cac_event_"
                "not_self_reversal"
            ),
        ),
        Index(
            "ix_cac_event_company",
            "company_id",
        ),
        Index(
            "ix_cac_event_settlement",
            "payment_settlement_allocation_id",
        ),
        Index(
            "ix_cac_event_liability",
            "sales_recognition_event_id",
        ),
        Index(
            "ix_cac_event_date",
            "clearing_date",
        ),
        Index(
            "ix_cac_event_source_original",
            "company_id",
            "payment_settlement_allocation_id",
            "sales_recognition_event_id",
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

    sales_recognition_event_id: Mapped[
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
