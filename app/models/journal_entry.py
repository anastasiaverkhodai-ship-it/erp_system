from datetime import date, datetime
from enum import Enum

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Enum as SQLEnum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class JournalEntryStatus(str, Enum):
    DRAFT = "draft"
    POSTED = "posted"
    REVERSED = "reversed"


class JournalEntry(Base):
    __tablename__ = "journal_entries"


    __table_args__ = (
        UniqueConstraint(
            "reversal_of_id",
            name="uq_journal_entry_reversal_of",
        ),
        Index(
            "uq_journal_entry_original_document",
            "document_id",
            unique=True,
            postgresql_where=text(
                "reversal_of_id IS NULL "
                "AND document_id IS NOT NULL"
            ),
        ),
        ForeignKeyConstraint(
            [
                "company_id",
                "payment_id",
            ],
            [
                "payments.company_id",
                "payments.id",
            ],
            name=(
                "fk_journal_entries_"
                "company_payment"
            ),
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "company_id",
                "payment_settlement_allocation_id",
            ],
            [
                "payment_settlement_allocations.company_id",
                "payment_settlement_allocations.id",
            ],
            name=(
                "fk_journal_entries_"
                "company_payment_settlement_allocation"
            ),
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "company_id",
                "tax_recognition_event_id",
            ],
            [
                "tax_recognition_events.company_id",
                "tax_recognition_events.id",
            ],
            name=(
                "fk_journal_entries_"
                "company_tax_recognition_event"
            ),
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "company_id",
                "sales_recognition_event_id",
            ],
            [
                "sales_recognition_events.company_id",
                "sales_recognition_events.id",
            ],
            name=(
                "fk_journal_entries_"
                "company_sales_recognition_event"
            ),
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "company_id",
                "vat_advance_bridge_event_id",
            ],
            [
                "vat_advance_bridge_events.company_id",
                "vat_advance_bridge_events.id",
            ],
            name=(
                "fk_journal_entries_"
                "company_vat_advance_bridge_event"
            ),
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "company_id",
                "input_vat_fulfillment_bridge_event_id",
            ],
            [
                "input_vat_fulfillment_bridge_events.company_id",
                "input_vat_fulfillment_bridge_events.id",
            ],
            name=(
                "fk_journal_entries_company_"
                "input_vat_fulfillment_bridge_event"
            ),
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "company_id",
                "supplier_advance_clearing_event_id",
            ],
            [
                "supplier_advance_clearing_events.company_id",
                "supplier_advance_clearing_events.id",
            ],
            name=(
                "fk_journal_entries_company_"
                "supplier_advance_clearing_event"
            ),
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "company_id",
                "customer_advance_clearing_event_id",
            ],
            [
                "customer_advance_clearing_events.company_id",
                "customer_advance_clearing_events.id",
            ],
            name=(
                "fk_journal_entries_company_"
                "customer_advance_clearing_event"
            ),
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "company_id",
                "sales_return_recognition_event_id",
            ],
            [
                "sales_return_recognition_events.company_id",
                "sales_return_recognition_events.id",
            ],
            name=(
                "fk_journal_entries_company_"
                "sales_return_recognition_event"
            ),
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "company_id",
                "sales_return_cost_restoration_event_id",
            ],
            [
                (
                    "sales_return_cost_restoration_events."
                    "company_id"
                ),
                (
                    "sales_return_cost_restoration_events."
                    "id"
                ),
            ],
            name=(
                "fk_journal_entries_company_"
                "sales_return_cost_restoration_event"
            ),
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "company_id",
                "purchase_return_recognition_event_id",
            ],
            [
                "purchase_return_recognition_events.company_id",
                "purchase_return_recognition_events.id",
            ],
            name=(
                "fk_journal_entries_company_"
                "purchase_return_recognition_event"
            ),
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            """
            (
                document_id IS NULL
                OR payment_id IS NULL
            )
            AND
            (
                document_id IS NULL
                OR payment_settlement_allocation_id IS NULL
            )
            AND
            (
                document_id IS NULL
                OR tax_recognition_event_id IS NULL
            )
            AND
            (
                document_id IS NULL
                OR sales_recognition_event_id IS NULL
            )
            AND
            (
                document_id IS NULL
                OR vat_advance_bridge_event_id IS NULL
            )
            AND
            (
                document_id IS NULL
                OR input_vat_fulfillment_bridge_event_id IS NULL
            )
            AND
            (
                document_id IS NULL
                OR supplier_advance_clearing_event_id IS NULL
            )
            AND
            (
                document_id IS NULL
                OR customer_advance_clearing_event_id IS NULL
            )
            AND
            (
                document_id IS NULL
                OR sales_return_recognition_event_id IS NULL
            )
            AND
            (
                document_id IS NULL
                OR sales_return_cost_restoration_event_id IS NULL
            )
            AND
            (
                document_id IS NULL
                OR purchase_return_recognition_event_id IS NULL
            )
            AND
            (
                payment_id IS NULL
                OR payment_settlement_allocation_id IS NULL
            )
            AND
            (
                payment_id IS NULL
                OR tax_recognition_event_id IS NULL
            )
            AND
            (
                payment_id IS NULL
                OR sales_recognition_event_id IS NULL
            )
            AND
            (
                payment_id IS NULL
                OR vat_advance_bridge_event_id IS NULL
            )
            AND
            (
                payment_id IS NULL
                OR input_vat_fulfillment_bridge_event_id IS NULL
            )
            AND
            (
                payment_id IS NULL
                OR supplier_advance_clearing_event_id IS NULL
            )
            AND
            (
                payment_id IS NULL
                OR customer_advance_clearing_event_id IS NULL
            )
            AND
            (
                payment_id IS NULL
                OR sales_return_recognition_event_id IS NULL
            )
            AND
            (
                payment_id IS NULL
                OR sales_return_cost_restoration_event_id IS NULL
            )
            AND
            (
                payment_id IS NULL
                OR purchase_return_recognition_event_id IS NULL
            )
            AND
            (
                payment_settlement_allocation_id IS NULL
                OR tax_recognition_event_id IS NULL
            )
            AND
            (
                payment_settlement_allocation_id IS NULL
                OR sales_recognition_event_id IS NULL
            )
            AND
            (
                payment_settlement_allocation_id IS NULL
                OR vat_advance_bridge_event_id IS NULL
            )
            AND
            (
                payment_settlement_allocation_id IS NULL
                OR input_vat_fulfillment_bridge_event_id IS NULL
            )
            AND
            (
                payment_settlement_allocation_id IS NULL
                OR supplier_advance_clearing_event_id IS NULL
            )
            AND
            (
                payment_settlement_allocation_id IS NULL
                OR customer_advance_clearing_event_id IS NULL
            )
            AND
            (
                payment_settlement_allocation_id IS NULL
                OR sales_return_recognition_event_id IS NULL
            )
            AND
            (
                payment_settlement_allocation_id IS NULL
                OR sales_return_cost_restoration_event_id IS NULL
            )
            AND
            (
                payment_settlement_allocation_id IS NULL
                OR purchase_return_recognition_event_id IS NULL
            )
            AND
            (
                tax_recognition_event_id IS NULL
                OR sales_recognition_event_id IS NULL
            )
            AND
            (
                tax_recognition_event_id IS NULL
                OR vat_advance_bridge_event_id IS NULL
            )
            AND
            (
                tax_recognition_event_id IS NULL
                OR input_vat_fulfillment_bridge_event_id IS NULL
            )
            AND
            (
                tax_recognition_event_id IS NULL
                OR supplier_advance_clearing_event_id IS NULL
            )
            AND
            (
                tax_recognition_event_id IS NULL
                OR customer_advance_clearing_event_id IS NULL
            )
            AND
            (
                tax_recognition_event_id IS NULL
                OR sales_return_recognition_event_id IS NULL
            )
            AND
            (
                tax_recognition_event_id IS NULL
                OR sales_return_cost_restoration_event_id IS NULL
            )
            AND
            (
                tax_recognition_event_id IS NULL
                OR purchase_return_recognition_event_id IS NULL
            )
            AND
            (
                sales_recognition_event_id IS NULL
                OR vat_advance_bridge_event_id IS NULL
            )
            AND
            (
                sales_recognition_event_id IS NULL
                OR input_vat_fulfillment_bridge_event_id IS NULL
            )
            AND
            (
                sales_recognition_event_id IS NULL
                OR supplier_advance_clearing_event_id IS NULL
            )
            AND
            (
                sales_recognition_event_id IS NULL
                OR customer_advance_clearing_event_id IS NULL
            )
            AND
            (
                sales_recognition_event_id IS NULL
                OR sales_return_recognition_event_id IS NULL
            )
            AND
            (
                sales_recognition_event_id IS NULL
                OR sales_return_cost_restoration_event_id IS NULL
            )
            AND
            (
                sales_recognition_event_id IS NULL
                OR purchase_return_recognition_event_id IS NULL
            )
            AND
            (
                vat_advance_bridge_event_id IS NULL
                OR input_vat_fulfillment_bridge_event_id IS NULL
            )
            AND
            (
                vat_advance_bridge_event_id IS NULL
                OR supplier_advance_clearing_event_id IS NULL
            )
            AND
            (
                vat_advance_bridge_event_id IS NULL
                OR customer_advance_clearing_event_id IS NULL
            )
            AND
            (
                vat_advance_bridge_event_id IS NULL
                OR sales_return_recognition_event_id IS NULL
            )
            AND
            (
                vat_advance_bridge_event_id IS NULL
                OR sales_return_cost_restoration_event_id IS NULL
            )
            AND
            (
                vat_advance_bridge_event_id IS NULL
                OR purchase_return_recognition_event_id IS NULL
            )
            AND
            (
                input_vat_fulfillment_bridge_event_id IS NULL
                OR supplier_advance_clearing_event_id IS NULL
            )
            AND
            (
                input_vat_fulfillment_bridge_event_id IS NULL
                OR customer_advance_clearing_event_id IS NULL
            )
            AND
            (
                input_vat_fulfillment_bridge_event_id IS NULL
                OR sales_return_recognition_event_id IS NULL
            )
            AND
            (
                input_vat_fulfillment_bridge_event_id IS NULL
                OR sales_return_cost_restoration_event_id IS NULL
            )
            AND
            (
                input_vat_fulfillment_bridge_event_id IS NULL
                OR purchase_return_recognition_event_id IS NULL
            )
            AND
            (
                supplier_advance_clearing_event_id IS NULL
                OR customer_advance_clearing_event_id IS NULL
            )
            AND
            (
                supplier_advance_clearing_event_id IS NULL
                OR sales_return_recognition_event_id IS NULL
            )
            AND
            (
                supplier_advance_clearing_event_id IS NULL
                OR sales_return_cost_restoration_event_id IS NULL
            )
            AND
            (
                supplier_advance_clearing_event_id IS NULL
                OR purchase_return_recognition_event_id IS NULL
            )
            AND
            (
                customer_advance_clearing_event_id IS NULL
                OR sales_return_recognition_event_id IS NULL
            )
            AND
            (
                customer_advance_clearing_event_id IS NULL
                OR sales_return_cost_restoration_event_id IS NULL
            )
            AND
            (
                customer_advance_clearing_event_id IS NULL
                OR purchase_return_recognition_event_id IS NULL
            )
            AND
            (
                sales_return_recognition_event_id IS NULL
                OR sales_return_cost_restoration_event_id IS NULL
            )
            AND
            (
                sales_return_recognition_event_id IS NULL
                OR purchase_return_recognition_event_id IS NULL
            )
            AND
            (
                sales_return_cost_restoration_event_id IS NULL
                OR purchase_return_recognition_event_id IS NULL
            )
            """,
            name=(
                "ck_journal_entries_"
                "at_most_one_business_source"
            ),
        ),
        Index(
            "uq_journal_entry_original_payment",
            "payment_id",
            unique=True,
            postgresql_where=text(
                "reversal_of_id IS NULL "
                "AND payment_id IS NOT NULL"
            ),
        ),
        Index(
            (
                "uq_journal_entry_original_"
                "payment_settlement_allocation"
            ),
            "payment_settlement_allocation_id",
            unique=True,
            postgresql_where=text(
                "reversal_of_id IS NULL "
                "AND payment_settlement_allocation_id "
                "IS NOT NULL"
            ),
        ),
        Index(
            (
                "uq_journal_entry_original_"
                "tax_recognition_event"
            ),
            "tax_recognition_event_id",
            unique=True,
            postgresql_where=text(
                "reversal_of_id IS NULL "
                "AND tax_recognition_event_id "
                "IS NOT NULL"
            ),
        ),
        Index(
            (
                "uq_journal_entry_original_"
                "sales_recognition_event"
            ),
            "sales_recognition_event_id",
            unique=True,
            postgresql_where=text(
                "reversal_of_id IS NULL "
                "AND sales_recognition_event_id "
                "IS NOT NULL"
            ),
        ),
        Index(
            (
                "uq_journal_entry_original_"
                "vat_advance_bridge_event"
            ),
            "vat_advance_bridge_event_id",
            unique=True,
            postgresql_where=text(
                "reversal_of_id IS NULL "
                "AND vat_advance_bridge_event_id "
                "IS NOT NULL"
            ),
        ),
        Index(
            (
                "uq_journal_entry_original_"
                "input_vat_fulfillment_bridge_event"
            ),
            "input_vat_fulfillment_bridge_event_id",
            unique=True,
            postgresql_where=text(
                "reversal_of_id IS NULL "
                "AND input_vat_fulfillment_bridge_event_id "
                "IS NOT NULL"
            ),
        ),
        Index(
            (
                "uq_journal_entry_original_"
                "supplier_advance_clearing_event"
            ),
            "supplier_advance_clearing_event_id",
            unique=True,
            postgresql_where=text(
                "reversal_of_id IS NULL "
                "AND supplier_advance_clearing_event_id "
                "IS NOT NULL"
            ),
        ),
        Index(
            (
                "uq_journal_entry_original_"
                "customer_advance_clearing_event"
            ),
            "customer_advance_clearing_event_id",
            unique=True,
            postgresql_where=text(
                "reversal_of_id IS NULL "
                "AND customer_advance_clearing_event_id "
                "IS NOT NULL"
            ),
        ),
        Index(
            (
                "uq_journal_entry_original_"
                "sales_return_recognition_event"
            ),
            "sales_return_recognition_event_id",
            unique=True,
            postgresql_where=text(
                "reversal_of_id IS NULL "
                "AND sales_return_recognition_event_id "
                "IS NOT NULL"
            ),
        ),
        Index(
            (
                "uq_journal_entry_original_"
                "sales_return_cost_restoration_event"
            ),
            "sales_return_cost_restoration_event_id",
            unique=True,
            postgresql_where=text(
                "reversal_of_id IS NULL "
                "AND sales_return_cost_restoration_event_id "
                "IS NOT NULL"
            ),
        ),        Index(
            (
                "uq_journal_entry_original_"
                "purchase_return_recognition_event"
            ),
            "purchase_return_recognition_event_id",
            unique=True,
            postgresql_where=text(
                "reversal_of_id IS NULL "
                "AND purchase_return_recognition_event_id "
                "IS NOT NULL"
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

    document_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "documents.id",
            ondelete="RESTRICT",
        ),
        nullable=True,
        index=True,
    )


    payment_id: Mapped[int | None] = mapped_column(
        nullable=True,
        index=True,
    )

    payment_settlement_allocation_id: Mapped[
        int | None
    ] = mapped_column(
        nullable=True,
        index=True,
    )
    tax_recognition_event_id: Mapped[
        int | None
    ] = mapped_column(
        nullable=True,
        index=True,
    )



    sales_recognition_event_id: Mapped[
        int | None
    ] = mapped_column(
        nullable=True,
        index=True,
    )

    vat_advance_bridge_event_id: Mapped[
        int | None
    ] = mapped_column(
        nullable=True,
        index=True,
    )

    input_vat_fulfillment_bridge_event_id: Mapped[
        int | None
    ] = mapped_column(
        nullable=True,
        index=True,
    )

    supplier_advance_clearing_event_id: Mapped[
        int | None
    ] = mapped_column(
        nullable=True,
        index=True,
    )

    customer_advance_clearing_event_id: Mapped[
        int | None
    ] = mapped_column(
        nullable=True,
        index=True,
    )

    sales_return_recognition_event_id: Mapped[
        int | None
    ] = mapped_column(
        nullable=True,
        index=True,
    )

    sales_return_cost_restoration_event_id: Mapped[
        int | None
    ] = mapped_column(
        nullable=True,
        index=True,
    )

    purchase_return_recognition_event_id: Mapped[
        int | None
    ] = mapped_column(
        nullable=True,
        index=True,
    )

    accounting_rule_id: Mapped[int | None] = mapped_column(
    ForeignKey(
        "accounting_rules.id",
        ondelete="RESTRICT",
    ),
    nullable=True,
    index=True,
)

    entry_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
        index=True,
    )

    description: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )

    status: Mapped[JournalEntryStatus] = mapped_column(
        SQLEnum(
            JournalEntryStatus,
            name="journal_entry_status_enum",
            native_enum=False,
            values_callable=lambda enum: [
                item.value for item in enum
            ],
        ),
        default=JournalEntryStatus.DRAFT,
        nullable=False,
        index=True,
    )

    created_by: Mapped[int] = mapped_column(
        ForeignKey(
            "users.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )

    posted_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )

    reversed_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )

    reversed_by: Mapped[int | None] = mapped_column(
        ForeignKey(
            "users.id",
            ondelete="RESTRICT",
        ),
        nullable=True,
    )

    reversal_of_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "journal_entries.id",
            ondelete="RESTRICT",
        ),
        nullable=True,
    )

    lines = relationship(
        "JournalEntryLine",
        back_populates="journal_entry",
        cascade="all, delete-orphan",
    )
