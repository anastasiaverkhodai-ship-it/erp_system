"""Append-only, effective-dated VAT settings; never infer registration from an INN."""
from datetime import date, datetime
from sqlalchemy import Boolean, CheckConstraint, Date, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


class CompanyVatPolicy(Base):
    __tablename__ = 'company_vat_policies'
    __table_args__ = (
        UniqueConstraint('company_id', 'id', name='uq_cvp_company_id'),
        UniqueConstraint('company_id', 'effective_from', name='uq_cvp_company_date'),
        CheckConstraint("payer_status IN ('vat_payer', 'non_vat_payer')", name='ck_cvp_status'),
        CheckConstraint("length(trim(legal_basis)) > 0", name='ck_cvp_basis'),
        CheckConstraint("(payer_status = 'vat_payer' AND vat_number IS NOT NULL AND length(trim(vat_number)) > 0) OR (payer_status = 'non_vat_payer' AND vat_number IS NULL AND NOT allow_cash_method)", name='ck_cvp_registration'),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey('companies.id', ondelete='RESTRICT'))
    effective_from: Mapped[date] = mapped_column(Date)
    payer_status: Mapped[str] = mapped_column(String(20))
    vat_number: Mapped[str | None] = mapped_column(String(20))
    legal_basis: Mapped[str] = mapped_column(String(500))
    allow_cash_method: Mapped[bool] = mapped_column(Boolean, default=False, server_default='false')
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id', ondelete='RESTRICT'))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
