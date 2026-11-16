"""Immutable attribution of a newly posted net payroll payment."""
from datetime import datetime
from sqlalchemy import Integer, Text, DateTime, ForeignKeyConstraint, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


class PayrollPaidIncome(Base):
    __tablename__ = 'payroll_paid_income'
    __table_args__ = (
        UniqueConstraint('company_id', 'payroll_disbursement_id', name='uq_paid_income_payment'),
        ForeignKeyConstraint(['company_id','payroll_disbursement_id'],
            ['payroll_disbursements.company_id','payroll_disbursements.id'],
            name='fk_paid_income_payment', ondelete='RESTRICT'),
        ForeignKeyConstraint(['company_id','journal_entry_id'],
            ['journal_entries.company_id','journal_entries.id'],
            name='fk_paid_income_journal', ondelete='RESTRICT'),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(Integer, nullable=False)
    payroll_disbursement_id: Mapped[int] = mapped_column(Integer, nullable=False)
    journal_entry_id: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
