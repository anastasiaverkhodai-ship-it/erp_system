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


class PurchaseReturnRecognitionEvent(Base):
    """
    Immutable economic Purchase Return recognition event.

    Provenance is the pair:

        TradeReturnEvent
        +
        InvoiceFulfillmentAllocation

    TradeReturnEvent is the immutable physical PURCHASE return fact.

    InvoiceFulfillmentAllocation is the existing durable economic
    purchase/receipt source identity used by supplier receipt-base
    allocation.

    returned_base_amount is the exact VAT-exclusive historical
    purchase-receipt accounting base allocated to this returned
    quantity. It is authoritative for Purchase Return inventory/AP
    accounting and MUST NOT be derived from:

        returned_gross_amount - returned_tax_amount

    because those monetary components are independently currency
    rounded and may differ by a cent for partial taxed returns.

    returned_gross_amount and returned_tax_amount are immutable
    commercial/tax snapshots only. They do not authorize VAT/RK
    accounting.

    Positive returned quantity may legitimately carry a zero monetary
    slice because cumulative currency rounding can defer a cent to a
    later quantity slice. Therefore base/gross/tax are nonnegative,
    not strictly positive.

    Historical rows are never updated or deleted. Reconciliation
    changes are represented by immutable reversal rows.
    """

    __tablename__ = "purchase_return_recognition_events"

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "id",
            name="uq_prre_event_company_id_id",
        ),
        UniqueConstraint(
            "company_id",
            "id",
            "trade_return_event_id",
            "invoice_fulfillment_allocation_id",
            name="uq_prre_event_company_id_id_source",
        ),
        UniqueConstraint(
            "reversal_of_id",
            name="uq_prre_event_reversal_of",
        ),
        ForeignKeyConstraint(
            (
                "company_id",
                "trade_return_event_id",
            ),
            (
                "trade_return_events.company_id",
                "trade_return_events.id",
            ),
            name="fk_prre_event_company_trade_return",
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
                "fk_prre_event_company_"
                "invoice_fulfillment_allocation"
            ),
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            (
                "company_id",
                "reversal_of_id",
                "trade_return_event_id",
                "invoice_fulfillment_allocation_id",
            ),
            (
                "purchase_return_recognition_events.company_id",
                "purchase_return_recognition_events.id",
                (
                    "purchase_return_recognition_events."
                    "trade_return_event_id"
                ),
                (
                    "purchase_return_recognition_events."
                    "invoice_fulfillment_allocation_id"
                ),
            ),
            name="fk_prre_event_reversal_source",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "returned_quantity > 0",
            name="ck_prre_event_returned_quantity_positive",
        ),
        CheckConstraint(
            "returned_base_amount >= 0",
            name="ck_prre_event_returned_base_nonnegative",
        ),
        CheckConstraint(
            "returned_gross_amount >= 0",
            name="ck_prre_event_returned_gross_nonnegative",
        ),
        CheckConstraint(
            "returned_tax_amount >= 0",
            name="ck_prre_event_returned_tax_nonnegative",
        ),
        CheckConstraint(
            "returned_tax_amount <= returned_gross_amount",
            name="ck_prre_event_tax_not_above_gross",
        ),
        CheckConstraint(
            "char_length(currency_code) = 3",
            name="ck_prre_event_currency_length",
        ),
        CheckConstraint(
            (
                "reversal_of_id IS NULL "
                "OR reversal_of_id <> id"
            ),
            name="ck_prre_event_not_self_reversal",
        ),
        Index(
            "ix_prre_event_pair_history",
            "company_id",
            "trade_return_event_id",
            "invoice_fulfillment_allocation_id",
            "id",
            unique=False,
        ),
        Index(
            "ix_prre_event_trade_return",
            "company_id",
            "trade_return_event_id",
            "recognition_date",
            "id",
            unique=False,
        ),
        Index(
            "ix_prre_event_invoice_fulfillment",
            "company_id",
            "invoice_fulfillment_allocation_id",
            "recognition_date",
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

    trade_return_event_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
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

    returned_quantity: Mapped[Decimal] = mapped_column(
        Numeric(
            18,
            4,
        ),
        nullable=False,
    )

    returned_base_amount: Mapped[Decimal] = mapped_column(
        Numeric(
            18,
            2,
        ),
        nullable=False,
    )

    returned_gross_amount: Mapped[Decimal] = mapped_column(
        Numeric(
            18,
            2,
        ),
        nullable=False,
    )

    returned_tax_amount: Mapped[Decimal] = mapped_column(
        Numeric(
            18,
            2,
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

    reversal_of_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
