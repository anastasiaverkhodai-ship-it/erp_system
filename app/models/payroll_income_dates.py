"""Immutable accountant-provided dates for otherwise undated adjustments."""
from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, ForeignKeyConstraint, Integer, JSON, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


class PayrollIncomeDateRequest(Base):
    __tablename__ = 'payroll_income_date_requests'
    __table_args__ = (
        UniqueConstraint('company_id','id',name='uq_income_date_request_company'),
        UniqueConstraint('company_id','request_key',name='uq_income_date_request_key'),
        ForeignKeyConstraint(['company_id','line_id'],['payroll_calculation_lines.company_id','payroll_calculation_lines.id'],name='fk_income_date_request_line'),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey('companies.id'),nullable=False)
    line_id: Mapped[int] = mapped_column(Integer,nullable=False)
    request_key: Mapped[str] = mapped_column(String(90),nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64),nullable=False)
    payload: Mapped[dict] = mapped_column(JSON,nullable=False)
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id'),nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),server_default=func.now(),nullable=False)


class PayrollIncomeDateApproval(Base):
    __tablename__ = 'payroll_income_date_approvals'
    __table_args__ = (
        UniqueConstraint('company_id','line_id',name='uq_income_date_approved_line'),
        UniqueConstraint('company_id','request_id',name='uq_income_date_approved_request'),
        ForeignKeyConstraint(['company_id','request_id'],['payroll_income_date_requests.company_id','payroll_income_date_requests.id'],name='fk_income_date_approval_request'),
        ForeignKeyConstraint(['company_id','line_id'],['payroll_calculation_lines.company_id','payroll_calculation_lines.id'],name='fk_income_date_approval_line'),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey('companies.id'),nullable=False)
    line_id: Mapped[int] = mapped_column(Integer,nullable=False)
    request_id: Mapped[int] = mapped_column(Integer,nullable=False)
    reviewed_by: Mapped[int] = mapped_column(ForeignKey('users.id'),nullable=False)
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),server_default=func.now(),nullable=False)
