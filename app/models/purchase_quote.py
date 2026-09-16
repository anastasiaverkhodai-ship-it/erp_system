"""Immutable supplier offer terms; withdrawal changes availability only."""
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, CheckConstraint, Date, DateTime, ForeignKey, ForeignKeyConstraint, Index, Numeric, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


class PurchaseQuote(Base):
    __tablename__ = "purchase_quotes"
    __table_args__ = (
        UniqueConstraint("company_id", "supplier_id", "reference", "product_id", name="uq_pq_reference_product"),
        ForeignKeyConstraint(["company_id", "supplier_id"], ["counterparties.company_id", "counterparties.id"], name="fk_pq_supplier", ondelete="RESTRICT"),
        ForeignKeyConstraint(["company_id", "supplier_id", "contract_id"], ["contracts.company_id", "contracts.counterparty_id", "contracts.id"], name="fk_pq_contract", ondelete="RESTRICT"),
        ForeignKeyConstraint(["company_id", "product_id"], ["products.company_id", "products.id"], name="fk_pq_product", ondelete="RESTRICT"),
        CheckConstraint("valid_until >= valid_from", name="ck_pq_dates"),
        CheckConstraint("min_quantity > 0 AND min_quantity < 'Infinity'::numeric", name="ck_pq_min_qty"),
        CheckConstraint("max_quantity IS NULL OR (max_quantity >= min_quantity AND max_quantity < 'Infinity'::numeric)", name="ck_pq_max_qty"),
        CheckConstraint("unit_price_net >= 0 AND unit_price_gross >= unit_price_net AND unit_price_gross < 'Infinity'::numeric", name="ck_pq_prices"),
        CheckConstraint("delivery_net >= 0 AND delivery_gross >= delivery_net AND delivery_gross < 'Infinity'::numeric", name="ck_pq_delivery"),
        CheckConstraint("lead_time_days >= 0 AND payment_term_days >= 0", name="ck_pq_terms"),
        CheckConstraint("currency_code = 'UAH'", name="ck_pq_currency"),
        Index("ix_pq_company_product_active", "company_id", "product_id", "is_active"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="RESTRICT"))
    supplier_id: Mapped[int] = mapped_column(nullable=False)
    contract_id: Mapped[int | None] = mapped_column(nullable=True)
    product_id: Mapped[int] = mapped_column(nullable=False)
    reference: Mapped[str] = mapped_column(String(100), nullable=False)
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False)
    min_quantity: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    max_quantity: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    unit_price_net: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    unit_price_gross: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    delivery_net: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    delivery_gross: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    lead_time_days: Mapped[int] = mapped_column(nullable=False)
    payment_term_days: Mapped[int] = mapped_column(nullable=False)
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_until: Mapped[date] = mapped_column(Date, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    withdrawn_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
