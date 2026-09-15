from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint, Date, DateTime, ForeignKey, ForeignKeyConstraint, Index,
    Numeric, String, UniqueConstraint, func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class PurchaseLandedCostValuationEvent(Base):
    """Append-only monetary destination; a reversal negates its original.

    Physical quantities and historical base valuation are never changed here.
    journal_entry_id supplies the exact GL provenance in both directions.
    """
    __tablename__ = "purchase_landed_cost_valuation_events"
    __table_args__ = (
        UniqueConstraint("company_id", "id", name="uq_plcv_company_id"),
        UniqueConstraint("reversal_of_id", name="uq_plcv_reversal"),
        UniqueConstraint("journal_entry_id", name="uq_plcv_journal"),
        ForeignKeyConstraint(
            ["company_id", "reversal_of_id"],
            ["purchase_landed_cost_valuation_events.company_id", "purchase_landed_cost_valuation_events.id"],
            name="fk_plcv_reversal", ondelete="RESTRICT",
        ),
        CheckConstraint("amount > 0 AND amount < 'Infinity'::numeric", name="ck_plcv_amount"),
        CheckConstraint("destination_kind IN ('on_hand', 'issued')", name="ck_plcv_kind"),
        CheckConstraint("reversal_of_id IS NULL OR reversal_of_id <> id", name="ck_plcv_not_self"),
        Index("ix_plcv_allocation", "company_id", "allocation_event_id", "id"),
        Index("ix_plcv_issue", "company_id", "inventory_cost_entry_id", "recognition_date"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="RESTRICT"))
    allocation_event_id: Mapped[int] = mapped_column(
        ForeignKey("purchase_landed_cost_allocation_events.id", ondelete="RESTRICT"), nullable=False,
    )
    destination_key: Mapped[str] = mapped_column(String(100), nullable=False)
    destination_kind: Mapped[str] = mapped_column(String(10), nullable=False)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="RESTRICT"))
    warehouse_id: Mapped[int] = mapped_column(ForeignKey("warehouses.id", ondelete="RESTRICT"))
    stock_lot_id: Mapped[int | None] = mapped_column(ForeignKey("stock_lots.id", ondelete="RESTRICT"))
    inventory_cost_entry_id: Mapped[int | None] = mapped_column(
        ForeignKey("inventory_cost_entries.id", ondelete="RESTRICT"),
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    recognition_date: Mapped[date] = mapped_column(Date, nullable=False)
    debit_account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="RESTRICT"))
    credit_account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="RESTRICT"))
    journal_entry_id: Mapped[int | None] = mapped_column(
        ForeignKey("journal_entries.id", ondelete="RESTRICT"),
    )
    reversal_of_id: Mapped[int | None] = mapped_column(nullable=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
