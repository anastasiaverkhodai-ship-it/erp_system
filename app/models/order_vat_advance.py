"""Whole-payment binding to a confirmed order; immutable amounts with audited lifecycle."""
from datetime import date, datetime
from decimal import Decimal
from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, ForeignKeyConstraint, Numeric, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


class OrderVatAdvance(Base):
    __tablename__='order_vat_advances'
    __table_args__=(
        UniqueConstraint('company_id','id',name='uq_ova_company_id'),
        UniqueConstraint('company_id','payment_id',name='uq_ova_payment'),
        ForeignKeyConstraint(['company_id','payment_id'],['payments.company_id','payments.id'],name='fk_ova_payment',ondelete='RESTRICT'),
        ForeignKeyConstraint(['company_id','order_id'],['trade_documents.company_id','trade_documents.id'],name='fk_ova_order',ondelete='RESTRICT'),
        ForeignKeyConstraint(['company_id','invoice_id'],['trade_documents.company_id','trade_documents.id'],name='fk_ova_invoice',ondelete='RESTRICT'),
        CheckConstraint("status IN ('active','transferred','reversed')",name='ck_ova_status'),
        CheckConstraint("amount > 0 AND amount < 'Infinity'::numeric",name='ck_ova_amount'),
        CheckConstraint("(status='active' AND invoice_id IS NULL AND closed_at IS NULL AND closed_by IS NULL) OR (status='transferred' AND invoice_id IS NOT NULL AND closed_at IS NOT NULL AND closed_by IS NOT NULL) OR (status='reversed' AND invoice_id IS NULL AND closed_at IS NOT NULL AND closed_by IS NOT NULL)",name='ck_ova_lifecycle'),
    )
    id: Mapped[int]=mapped_column(primary_key=True)
    company_id: Mapped[int]=mapped_column(nullable=False)
    payment_id: Mapped[int]=mapped_column(nullable=False)
    order_id: Mapped[int]=mapped_column(nullable=False)
    invoice_id: Mapped[int|None]=mapped_column(nullable=True)
    status: Mapped[str]=mapped_column(String(20),default='active',server_default='active')
    amount: Mapped[Decimal]=mapped_column(Numeric(18,2))
    event_date: Mapped[date]=mapped_column(Date)
    created_by: Mapped[int]=mapped_column(ForeignKey('users.id',ondelete='RESTRICT'))
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),server_default=func.now())
    closed_by: Mapped[int|None]=mapped_column(ForeignKey('users.id',ondelete='RESTRICT'))
    closed_at: Mapped[datetime|None]=mapped_column(DateTime(timezone=True))


class OrderVatAdvanceLine(Base):
    __tablename__='order_vat_advance_lines'
    __table_args__=(
        UniqueConstraint('advance_id','tax_calculation_id',name='uq_oval_calculation'),
        ForeignKeyConstraint(['company_id','advance_id'],['order_vat_advances.company_id','order_vat_advances.id'],name='fk_oval_advance',ondelete='RESTRICT'),
        ForeignKeyConstraint(['company_id','tax_calculation_id'],['tax_calculations.company_id','tax_calculations.id'],name='fk_oval_calc',ondelete='RESTRICT'),
        CheckConstraint("base >= 0 AND tax >= 0 AND gross > 0 AND gross < 'Infinity'::numeric AND gross = base + tax",name='ck_oval_amounts'),
    )
    id: Mapped[int]=mapped_column(primary_key=True)
    company_id: Mapped[int]=mapped_column(nullable=False)
    advance_id: Mapped[int]=mapped_column(nullable=False)
    tax_calculation_id: Mapped[int]=mapped_column(nullable=False)
    base: Mapped[Decimal]=mapped_column(Numeric(18,2))
    tax: Mapped[Decimal]=mapped_column(Numeric(18,2))
    gross: Mapped[Decimal]=mapped_column(Numeric(18,2))


class OrderVatAdvanceTransfer(Base):
    """Append-only transfer identity; a reversal closes, never deletes, the link."""
    __tablename__ = 'order_vat_advance_transfers'
    __table_args__ = (
        ForeignKeyConstraint(['company_id', 'advance_id'], ['order_vat_advances.company_id', 'order_vat_advances.id'], name='fk_ovat_advance', ondelete='RESTRICT'),
        ForeignKeyConstraint(['company_id', 'allocation_id'], ['payment_settlement_allocations.company_id', 'payment_settlement_allocations.id'], name='fk_ovat_allocation', ondelete='RESTRICT'),
        UniqueConstraint('allocation_id', name='uq_ovat_allocation'),
        CheckConstraint('(reversed_at IS NULL) = (reversed_by IS NULL)', name='ck_ovat_reversal'),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(nullable=False)
    advance_id: Mapped[int] = mapped_column(nullable=False)
    allocation_id: Mapped[int] = mapped_column(nullable=False)
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id', ondelete='RESTRICT'))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    reversed_by: Mapped[int | None] = mapped_column(ForeignKey('users.id', ondelete='RESTRICT'))
    reversed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
