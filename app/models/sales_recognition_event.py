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


class SalesRecognitionEvent(Base):
    """
    Persistent immutable commercial Sales recognition ledger event.

    invoice_fulfillment_allocation_id identifies the durable
    quantity-level match between a confirmed Sales Invoice line
    and an actual posted warehouse fulfillment.

    recognized_gross_amount is the tax-inclusive commercial amount
    represented by this event.

    recognized_tax_amount is the VAT economically contained in the
    recognized commercial amount. It is retained as an immutable
    monetary snapshot for later Sales/VAT bridge reconciliation;
    it does not itself determine the VAT journal entry.

    Normal persistence keeps at most one ACTIVE original event for
    one fulfillment allocation. If its desired monetary target changes,
    the active original is reversed and one full replacement is created.
    Historical reversed originals remain immutable.

    Reversal is represented by another immutable event through
    reversal_of_id. A reversal must belong to the same company and
    the same InvoiceFulfillmentAllocation as the original event.

    Recognition balances are derived from immutable events.
    No mutable recognized / remaining balance is stored here.
    """

    __tablename__ = "sales_recognition_events"

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "id",
            name=(
                "uq_sales_recognition_events_"
                "company_id_id"
            ),
        ),
        UniqueConstraint(
            "company_id",
            "id",
            "invoice_fulfillment_allocation_id",
            name=(
                "uq_sales_recognition_events_"
                "company_id_id_fulfillment_source"
            ),
        ),
        UniqueConstraint(
            "reversal_of_id",
            name=(
                "uq_sales_recognition_events_"
                "reversal_of"
            ),
        ),
        ForeignKeyConstraint(
            [
                "company_id",
                "invoice_fulfillment_allocation_id",
            ],
            [
                "invoice_fulfillment_allocations.company_id",
                "invoice_fulfillment_allocations.id",
            ],
            name=(
                "fk_sales_recognition_events_"
                "company_fulfillment_source"
            ),
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "company_id",
                "reversal_of_id",
                "invoice_fulfillment_allocation_id",
            ],
            [
                "sales_recognition_events.company_id",
                "sales_recognition_events.id",
                (
                    "sales_recognition_events."
                    "invoice_fulfillment_allocation_id"
                ),
            ],
            name=(
                "fk_sales_recognition_events_"
                "company_reversal_of_source"
            ),
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "recognized_quantity > 0",
            name=(
                "ck_sales_recognition_events_"
                "quantity_positive"
            ),
        ),
        CheckConstraint(
            "recognized_gross_amount > 0",
            name=(
                "ck_sales_recognition_events_"
                "gross_amount_positive"
            ),
        ),
        CheckConstraint(
            "recognized_tax_amount >= 0",
            name=(
                "ck_sales_recognition_events_"
                "tax_amount_nonnegative"
            ),
        ),
        CheckConstraint(
            (
                "recognized_tax_amount "
                "<= recognized_gross_amount"
            ),
            name=(
                "ck_sales_recognition_events_"
                "tax_not_above_gross"
            ),
        ),
        CheckConstraint(
            "char_length(currency_code) = 3",
            name=(
                "ck_sales_recognition_events_"
                "currency_code_length"
            ),
        ),
        CheckConstraint(
            (
                "reversal_of_id IS NULL "
                "OR reversal_of_id <> id"
            ),
            name=(
                "ck_sales_recognition_events_"
                "not_self_reversal"
            ),
        ),
        Index(
            (
                "ix_sales_recognition_events_"
                "fulfillment_source_original"
            ),
            "company_id",
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

    invoice_fulfillment_allocation_id: Mapped[
        int
    ] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )

    recognition_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
        index=True,
    )

    recognized_quantity: Mapped[
        Decimal
    ] = mapped_column(
        Numeric(
            precision=18,
            scale=4,
        ),
        nullable=False,
    )

    recognized_gross_amount: Mapped[
        Decimal
    ] = mapped_column(
        Numeric(
            precision=18,
            scale=2,
        ),
        nullable=False,
    )

    recognized_tax_amount: Mapped[
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
