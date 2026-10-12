"""Attribute an already posted insurance receipt to payroll receivables."""
from datetime import datetime
from decimal import Decimal
from sqlalchemy import DateTime, ForeignKey, ForeignKeyConstraint, Integer, Numeric, String, UniqueConstraint, CheckConstraint, func
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


class PayrollInsuranceFunding(Base):
    __tablename__ = 'payroll_insurance_funding'
    __table_args__ = (
        UniqueConstraint('company_id','id',name='uq_payroll_insurance_funding_company'),
        UniqueConstraint('company_id','request_key',name='uq_payroll_insurance_funding_request'),
        UniqueConstraint('company_id','journal_entry_id',name='uq_payroll_insurance_funding_journal'),
        ForeignKeyConstraint(['company_id','journal_entry_id'],['journal_entries.company_id','journal_entries.id'],name='fk_payroll_insurance_funding_journal',ondelete='RESTRICT'),
        ForeignKeyConstraint(['company_id','bank_account_id'],['bank_accounts.company_id','bank_accounts.id'],name='fk_payroll_insurance_funding_bank',ondelete='RESTRICT'),
        CheckConstraint('amount > 0',name='ck_payroll_insurance_funding_amount'),
        CheckConstraint('length(trim(document_reference)) > 0 AND length(trim(separate_account_reference)) > 0',name='ck_payroll_insurance_funding_references'),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey('companies.id',ondelete='RESTRICT'),nullable=False)
    journal_entry_id: Mapped[int] = mapped_column(Integer,nullable=False)
    bank_account_id: Mapped[int] = mapped_column(Integer,nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(18,2),nullable=False)
    request_key: Mapped[str] = mapped_column(String(200),nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64),nullable=False)
    document_reference: Mapped[str] = mapped_column(String(500),nullable=False)
    separate_account_reference: Mapped[str] = mapped_column(String(500),nullable=False)
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id',ondelete='RESTRICT'),nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),server_default=func.now(),nullable=False)


class PayrollInsuranceFundingLine(Base):
    __tablename__ = 'payroll_insurance_funding_lines'
    __table_args__ = (
        UniqueConstraint('company_id','funding_id','payroll_calculation_id',name='uq_payroll_insurance_funding_line_source'),
        ForeignKeyConstraint(['company_id','funding_id'],['payroll_insurance_funding.company_id','payroll_insurance_funding.id'],name='fk_payroll_insurance_funding_line_parent',ondelete='RESTRICT'),
        ForeignKeyConstraint(['company_id','payroll_calculation_id'],['payroll_calculations.company_id','payroll_calculations.id'],name='fk_payroll_insurance_funding_line_calculation',ondelete='RESTRICT'),
        CheckConstraint('amount > 0',name='ck_payroll_insurance_funding_line_amount'),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(Integer,nullable=False)
    funding_id: Mapped[int] = mapped_column(Integer,nullable=False)
    payroll_calculation_id: Mapped[int] = mapped_column(Integer,nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(18,2),nullable=False)
