"""Attribute a posted bank tax payment to payroll liabilities."""
from datetime import datetime
from decimal import Decimal
from sqlalchemy import DateTime, ForeignKey, ForeignKeyConstraint, Integer, Numeric, String, UniqueConstraint, CheckConstraint, func
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


class PayrollTaxRemittance(Base):
    __tablename__ = 'payroll_tax_remittance'
    __table_args__ = (
        UniqueConstraint('company_id','id',name='uq_payroll_tax_remittance_company'),
        UniqueConstraint('company_id','request_key',name='uq_payroll_tax_remittance_request'),
        UniqueConstraint('company_id','journal_entry_id',name='uq_payroll_tax_remittance_journal'),
        ForeignKeyConstraint(['company_id','journal_entry_id'],['journal_entries.company_id','journal_entries.id'],name='fk_payroll_tax_remittance_journal',ondelete='RESTRICT'),
        ForeignKeyConstraint(['company_id','bank_account_id'],['bank_accounts.company_id','bank_accounts.id'],name='fk_payroll_tax_remittance_bank',ondelete='RESTRICT'),
        CheckConstraint("component IN ('personal_income_tax','military_levy','unified_social_contribution')",name='ck_payroll_tax_remittance_component'),
        CheckConstraint('amount > 0',name='ck_payroll_tax_remittance_amount'),
        CheckConstraint('length(trim(document_reference)) > 0 AND length(trim(budget_payment_reference)) > 0',name='ck_payroll_tax_remittance_references'),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey('companies.id',ondelete='RESTRICT'),nullable=False)
    journal_entry_id: Mapped[int] = mapped_column(Integer,nullable=False)
    bank_account_id: Mapped[int] = mapped_column(Integer,nullable=False)
    component: Mapped[str] = mapped_column(String(64),nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(18,2),nullable=False)
    request_key: Mapped[str] = mapped_column(String(200),nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64),nullable=False)
    document_reference: Mapped[str] = mapped_column(String(500),nullable=False)
    budget_payment_reference: Mapped[str] = mapped_column(String(500),nullable=False)
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id',ondelete='RESTRICT'),nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),server_default=func.now(),nullable=False)


class PayrollTaxRemittanceLine(Base):
    __tablename__ = 'payroll_tax_remittance_lines'
    __table_args__ = (
        UniqueConstraint('company_id','remittance_id','payroll_calculation_id',name='uq_payroll_tax_remittance_line_source'),
        ForeignKeyConstraint(['company_id','remittance_id'],['payroll_tax_remittance.company_id','payroll_tax_remittance.id'],name='fk_payroll_tax_remittance_line_parent',ondelete='RESTRICT'),
        ForeignKeyConstraint(['company_id','payroll_calculation_id'],['payroll_calculations.company_id','payroll_calculations.id'],name='fk_payroll_tax_remittance_line_calculation',ondelete='RESTRICT'),
        CheckConstraint('amount > 0',name='ck_payroll_tax_remittance_line_amount'),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(Integer,nullable=False)
    remittance_id: Mapped[int] = mapped_column(Integer,nullable=False)
    payroll_calculation_id: Mapped[int] = mapped_column(Integer,nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(18,2),nullable=False)
