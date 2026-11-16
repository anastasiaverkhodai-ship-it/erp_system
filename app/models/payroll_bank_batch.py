"""Immutable consolidated bank reconciliation source and allocations."""
from datetime import datetime
from decimal import Decimal
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Numeric, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


class PayrollBankBatch(Base):
    __tablename__ = "payroll_bank_batches"
    __table_args__ = (
        UniqueConstraint("company_id", "id", name="uq_payroll_bank_batch_company_id"),
        UniqueConstraint("company_id", "request_key", name="uq_payroll_bank_batch_request"),
        ForeignKeyConstraint(["company_id", "bank_statement_line_id"], ["bank_statement_lines.company_id", "bank_statement_lines.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["company_id", "employee_id"], ["employees.company_id", "employees.id"], ondelete="RESTRICT"),
        CheckConstraint("amount > 0 AND currency_code = 'UAH'", name="ck_payroll_bank_batch_amount"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="RESTRICT"))
    employee_id: Mapped[int]
    bank_statement_line_id: Mapped[int]
    request_key: Mapped[str] = mapped_column(String(100))
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    currency_code: Mapped[str] = mapped_column(String(3))
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PayrollBankBatchAllocation(Base):
    __tablename__ = "payroll_bank_batch_allocations"
    __table_args__ = (
        UniqueConstraint("company_id", "batch_id", "payroll_disbursement_id", name="uq_payroll_bank_batch_member"),
        ForeignKeyConstraint(["company_id", "batch_id"], ["payroll_bank_batches.company_id", "payroll_bank_batches.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["company_id", "payroll_disbursement_id"], ["payroll_disbursements.company_id", "payroll_disbursements.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["company_id", "journal_entry_id"], ["journal_entries.company_id", "journal_entries.id"], ondelete="RESTRICT"),
        CheckConstraint("amount > 0", name="ck_payroll_bank_batch_allocation_amount"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int]
    batch_id: Mapped[int]
    payroll_disbursement_id: Mapped[int]
    journal_entry_id: Mapped[int]
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
