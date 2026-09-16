"""Effective-dated counterparty registration evidence, entered by authorized users."""
from datetime import date, datetime
from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, ForeignKeyConstraint, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


class CounterpartyVatRegistration(Base):
    __tablename__ = 'counterparty_vat_registrations'
    __table_args__ = (
        UniqueConstraint('company_id', 'counterparty_id', 'effective_from', name='uq_cvr_party_date'),
        UniqueConstraint('company_id', 'counterparty_id', 'id', name='uq_cvr_party_id'),
        ForeignKeyConstraint(['company_id','counterparty_id'], ['counterparties.company_id','counterparties.id'], ondelete='RESTRICT', name='fk_cvr_party'),
        CheckConstraint("payer_status IN ('vat_payer', 'non_vat_payer')", name='ck_cvr_status'),
        CheckConstraint("length(trim(legal_basis)) > 0", name='ck_cvr_basis'),
        CheckConstraint("(payer_status = 'vat_payer' AND vat_number IS NOT NULL AND length(trim(vat_number)) > 0) OR (payer_status = 'non_vat_payer' AND vat_number IS NULL)", name='ck_cvr_registration'),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(nullable=False)
    counterparty_id: Mapped[int] = mapped_column(nullable=False)
    effective_from: Mapped[date] = mapped_column(Date)
    payer_status: Mapped[str] = mapped_column(String(20))
    vat_number: Mapped[str | None] = mapped_column(String(20))
    legal_basis: Mapped[str] = mapped_column(String(500))
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id', ondelete='RESTRICT'))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
