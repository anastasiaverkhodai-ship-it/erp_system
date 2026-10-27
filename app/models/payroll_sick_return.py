"""Confirmed cash returns against a corrected payroll or insurance balance."""
from sqlalchemy import Column, Integer, String, Numeric, DateTime, ForeignKeyConstraint, CheckConstraint, UniqueConstraint, func
from app.core.database import Base


class PayrollSickReturn(Base):
    __tablename__ = 'payroll_sick_returns'
    id = Column(Integer, primary_key=True)
    company_id = Column(Integer, nullable=False)
    payroll_calculation_id = Column(Integer, nullable=False)
    journal_entry_id = Column(Integer, nullable=False)
    kind = Column(String(20), nullable=False)
    amount = Column(Numeric(18,2), nullable=False)
    request_key = Column(String(100), nullable=False)
    request_fingerprint = Column(String(64), nullable=False)
    document_reference = Column(String(500), nullable=False)
    created_by = Column(Integer, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    __table_args__ = (
        UniqueConstraint('company_id','journal_entry_id',name='uq_sick_return_journal'),
        UniqueConstraint('company_id','request_key',name='uq_sick_return_request'),
        ForeignKeyConstraint(['company_id','payroll_calculation_id'], ['payroll_calculations.company_id','payroll_calculations.id'],name='fk_sick_return_calculation',ondelete='RESTRICT'),
        ForeignKeyConstraint(['company_id','journal_entry_id'], ['journal_entries.company_id','journal_entries.id'],name='fk_sick_return_journal',ondelete='RESTRICT'),
        CheckConstraint("kind IN ('employee','insurer')",name='ck_sick_return_kind'),
        CheckConstraint("amount > 0 AND amount <> 'NaN'::numeric",name='ck_sick_return_amount'),
        CheckConstraint('length(trim(document_reference)) >= 10',name='ck_sick_return_reference'),
    )
