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
)
from sqlalchemy.orm import (
    Mapped,
    mapped_column,
)

from app.core.database import Base


class PurchaseValueCorrectionFifoImpactEvent(Base):
    """
    Immutable FIFO inventory/cost destination impact of one
    PurchaseValueCorrectionAllocationEvent.

    This event does not change physical warehouse quantity.

    destination_kind:

        on_hand
            Corrected purchase value still belongs to inventory
            remaining in the receipt StockLot.

        issued
            Corrected purchase value belongs to quantity already
            consumed from that StockLot by one economically active
            StockLotConsumption.

    Primary recognition_date:

        on_hand:
            PurchaseValueCorrectionAllocationEvent
            recognition_date.

        issued:
            max(
                PurchaseValueCorrectionAllocationEvent
                recognition_date,
                ISSUE Document.document_date,
            )

    Later monetary / topology changes use immutable reversal and
    replacement on the caller's forward adjustment_date.

    Historical rows are never updated or deleted.

    This foundation deliberately performs no JournalEntry,
    supplier 631, supplier clearing, VAT, physical inventory
    mutation, commit, or rollback.
    """

    __tablename__ = (
        "purchase_value_correction_fifo_impact_events"
    )

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "id",
            name="uq_pvcfi_event_company_id_id",
        ),
        UniqueConstraint(
            "reversal_of_id",
            name="uq_pvcfi_event_reversal_of",
        ),
        ForeignKeyConstraint(
            (
                "company_id",
            ),
            (
                "companies.id",
            ),
            name="fk_pvcfi_event_company",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            (
                "company_id",
                "purchase_value_correction_allocation_event_id",
            ),
            (
                "purchase_value_correction_allocation_events.company_id",
                "purchase_value_correction_allocation_events.id",
            ),
            name="fk_pvcfi_event_allocation",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            (
                "stock_lot_id",
            ),
            (
                "stock_lots.id",
            ),
            name="fk_pvcfi_event_stock_lot",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            (
                "stock_lot_consumption_id",
            ),
            (
                "stock_lot_consumptions.id",
            ),
            name="fk_pvcfi_event_consumption",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            (
                "issue_document_id",
            ),
            (
                "documents.id",
            ),
            name="fk_pvcfi_event_issue_document",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            (
                "issue_document_line_id",
            ),
            (
                "document_lines.id",
            ),
            name="fk_pvcfi_event_issue_line",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            (
                "created_by",
            ),
            (
                "users.id",
            ),
            name="fk_pvcfi_event_created_by",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            (
                "reversal_of_id",
            ),
            (
                "purchase_value_correction_fifo_impact_events.id",
            ),
            name="fk_pvcfi_event_reversal",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            (
                "destination_kind IN "
                "('issued', 'on_hand')"
            ),
            name="ck_pvcfi_event_destination_kind",
        ),
        CheckConstraint(
            "quantity > 0",
            name="ck_pvcfi_event_quantity_positive",
        ),
        CheckConstraint(
            "original_base_amount >= 0",
            name="ck_pvcfi_event_original_nonnegative",
        ),
        CheckConstraint(
            "corrected_base_amount >= 0",
            name="ck_pvcfi_event_corrected_nonnegative",
        ),
        CheckConstraint(
            (
                "original_base_amount "
                "<> corrected_base_amount"
            ),
            name="ck_pvcfi_event_not_noop",
        ),
        CheckConstraint(
            "char_length(currency_code) = 3",
            name="ck_pvcfi_event_currency_length",
        ),
        CheckConstraint(
            (
                "reversal_of_id IS NULL "
                "OR reversal_of_id <> id"
            ),
            name="ck_pvcfi_event_not_self_reversal",
        ),
        CheckConstraint(
            (
                "("
                "destination_kind = 'on_hand' "
                "AND stock_lot_consumption_id IS NULL "
                "AND issue_document_id IS NULL "
                "AND issue_document_line_id IS NULL"
                ") OR ("
                "destination_kind = 'issued' "
                "AND stock_lot_consumption_id IS NOT NULL "
                "AND issue_document_id IS NOT NULL "
                "AND issue_document_line_id IS NOT NULL"
                ")"
            ),
            name="ck_pvcfi_event_destination_provenance",
        ),
        Index(
            "ix_pvcfi_event_allocation",
            "company_id",
            "purchase_value_correction_allocation_event_id",
        ),
        Index(
            "ix_pvcfi_event_stock_lot",
            "company_id",
            "stock_lot_id",
        ),
        Index(
            "ix_pvcfi_event_consumption",
            "company_id",
            "stock_lot_consumption_id",
        ),
        Index(
            "ix_pvcfi_event_recognition_date",
            "company_id",
            "recognition_date",
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

    purchase_value_correction_allocation_event_id: Mapped[
        int
    ] = mapped_column(
        Integer,
        nullable=False,
    )

    stock_lot_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    destination_kind: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
    )

    stock_lot_consumption_id: Mapped[
        int | None
    ] = mapped_column(
        Integer,
        nullable=True,
    )

    issue_document_id: Mapped[
        int | None
    ] = mapped_column(
        Integer,
        nullable=True,
    )

    issue_document_line_id: Mapped[
        int | None
    ] = mapped_column(
        Integer,
        nullable=True,
    )

    recognition_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )

    quantity: Mapped[Decimal] = mapped_column(
        Numeric(
            18,
            4,
        ),
        nullable=False,
    )

    original_base_amount: Mapped[Decimal] = mapped_column(
        Numeric(
            18,
            2,
        ),
        nullable=False,
    )

    corrected_base_amount: Mapped[Decimal] = mapped_column(
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

    reversal_of_id: Mapped[
        int | None
    ] = mapped_column(
        Integer,
        nullable=True,
    )

    @property
    def base_amount_delta(
        self,
    ) -> Decimal:
        return (
            Decimal(
                self.corrected_base_amount
            )
            - Decimal(
                self.original_base_amount
            )
        )
