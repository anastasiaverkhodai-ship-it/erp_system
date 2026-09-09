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
from sqlalchemy.orm import (
    Mapped,
    mapped_column,
)

from app.core.database import Base


class PurchaseValueCorrectionAllocationEvent(Base):
    """
    Immutable economic allocation of one PURCHASE
    TradeValueCorrectionEvent to one
    InvoiceFulfillmentAllocation.

    This event is quantity-neutral.

    Commercial correction truth remains in
    TradeValueCorrectionEvent.

    Fulfillment quantity and receipt provenance remain in
    InvoiceFulfillmentAllocation and its warehouse source.

    recognition_date is determined by reconciliation as:

        max(
            TradeValueCorrectionEvent.correction_date,
            receipt economic document_date,
        )

    Therefore a correction that predates a future receipt does
    not create supplier liability or inventory value before the
    receipt itself exists.

    original_allocated_base_amount and
    corrected_allocated_base_amount are VAT-exclusive economic
    amounts allocated with cumulative-delta currency rounding.

    Historical rows are never updated or deleted.
    Reconciliation changes state by immutable reversal and,
    where necessary, replacement events.

    This foundation deliberately has no JournalEntry, inventory,
    supplier-clearing, or legal VAT side effects.
    """

    __tablename__ = (
        "purchase_value_correction_allocation_events"
    )

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "id",
            name="uq_pvca_event_company_id_id",
        ),
        UniqueConstraint(
            "company_id",
            "id",
            "trade_value_correction_event_id",
            "invoice_fulfillment_allocation_id",
            name="uq_pvca_event_company_id_id_source",
        ),
        UniqueConstraint(
            "reversal_of_id",
            name="uq_pvca_event_reversal_of",
        ),
        ForeignKeyConstraint(
            (
                "company_id",
            ),
            (
                "companies.id",
            ),
            name="fk_pvca_event_company",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            (
                "created_by",
            ),
            (
                "users.id",
            ),
            name="fk_pvca_event_created_by",
            ondelete="RESTRICT",
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
                "fk_pvca_event_"
                "trade_value_correction"
            ),
            ondelete="RESTRICT",
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
                "fk_pvca_event_"
                "invoice_fulfillment_allocation"
            ),
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            (
                "company_id",
                "reversal_of_id",
                "trade_value_correction_event_id",
                "invoice_fulfillment_allocation_id",
            ),
            (
                "purchase_value_correction_allocation_events.company_id",
                "purchase_value_correction_allocation_events.id",
                (
                    "purchase_value_correction_allocation_events."
                    "trade_value_correction_event_id"
                ),
                (
                    "purchase_value_correction_allocation_events."
                    "invoice_fulfillment_allocation_id"
                ),
            ),
            name="fk_pvca_event_reversal_source",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "original_allocated_base_amount >= 0",
            name=(
                "ck_pvca_event_"
                "original_base_nonnegative"
            ),
        ),
        CheckConstraint(
            "corrected_allocated_base_amount >= 0",
            name=(
                "ck_pvca_event_"
                "corrected_base_nonnegative"
            ),
        ),
        CheckConstraint(
            (
                "original_allocated_base_amount "
                "<> corrected_allocated_base_amount"
            ),
            name="ck_pvca_event_not_noop",
        ),
        CheckConstraint(
            "char_length(currency_code) = 3",
            name="ck_pvca_event_currency_length",
        ),
        CheckConstraint(
            (
                "reversal_of_id IS NULL "
                "OR reversal_of_id <> id"
            ),
            name="ck_pvca_event_not_self_reversal",
        ),
        Index(
            "ix_pvca_event_source",
            "company_id",
            "trade_value_correction_event_id",
            "invoice_fulfillment_allocation_id",
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

    invoice_fulfillment_allocation_id: Mapped[int] = (
        mapped_column(
            Integer,
            nullable=False,
        )
    )

    recognition_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )

    original_allocated_base_amount: Mapped[Decimal] = (
        mapped_column(
            Numeric(
                18,
                2,
            ),
            nullable=False,
        )
    )

    corrected_allocated_base_amount: Mapped[Decimal] = (
        mapped_column(
            Numeric(
                18,
                2,
            ),
            nullable=False,
        )
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
        DateTime(
            timezone=True,
        ),
        server_default=func.now(),
        nullable=False,
    )

    reversal_of_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    @property
    def allocated_base_delta(
        self,
    ) -> Decimal:
        return (
            Decimal(
                self.corrected_allocated_base_amount
            )
            - Decimal(
                self.original_allocated_base_amount
            )
        )
