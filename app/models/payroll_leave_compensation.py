"""Immutable termination compensation, linked to the consumed leave rights."""
from datetime import date, datetime
from decimal import Decimal
from sqlalchemy import BigInteger, text, CheckConstraint, Date, DateTime, ForeignKey, ForeignKeyConstraint, Integer, JSON, Numeric, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


class PayrollLeaveCompensation(Base):
    __tablename__='payroll_leave_compensations'
    __table_args__=(
        UniqueConstraint('company_id','id',name='uq_plc_company_id'),
        UniqueConstraint('company_id','request_key',name='uq_plc_request'),
        ForeignKeyConstraint(['company_id','employment_contract_id'],['employment_contracts.company_id','employment_contracts.id'],name='fk_plc_contract',ondelete='RESTRICT'),
        ForeignKeyConstraint(['company_id','termination_event_id'],['employment_events.company_id','employment_events.id'],name='fk_plc_termination',ondelete='RESTRICT'),
        ForeignKeyConstraint(['company_id','payroll_input_id'],['payroll_inputs.company_id','payroll_inputs.id'],name='fk_plc_input',ondelete='RESTRICT'),
        CheckConstraint("gross_amount >= 0 AND gross_amount <> 'NaN'::numeric",name='ck_plc_gross'),
    )
    id: Mapped[int]=mapped_column(primary_key=True)
    company_id: Mapped[int]=mapped_column(Integer,nullable=False)
    employment_contract_id: Mapped[int]=mapped_column(Integer,nullable=False)
    termination_event_id: Mapped[int]=mapped_column(Integer,nullable=False)
    payroll_input_id: Mapped[int]=mapped_column(Integer,nullable=False)
    compensation_date: Mapped[date]=mapped_column(Date,nullable=False)
    recovery_exemption: Mapped[dict | None]=mapped_column(JSON(none_as_null=True),nullable=True)
    creation_xid: Mapped[int]=mapped_column(BigInteger,nullable=False,server_default=text('txid_current()'))
    gross_amount: Mapped[Decimal]=mapped_column(Numeric(18,2),nullable=False)
    source_reference: Mapped[str]=mapped_column(String(1000),nullable=False)
    request_key: Mapped[str]=mapped_column(String(100),nullable=False)
    request_fingerprint: Mapped[str]=mapped_column(String(64),nullable=False)
    created_by: Mapped[int]=mapped_column(ForeignKey('users.id',ondelete='RESTRICT'),nullable=False)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),server_default=func.now(),nullable=False)


class PayrollLeaveCompensationLine(Base):
    __tablename__='payroll_leave_compensation_lines'
    __table_args__=(
        UniqueConstraint('company_id','id',name='uq_plcl_company_id'),
        UniqueConstraint('company_id','compensation_id','account_id','acquisition_bucket',name='uq_plcl_account_bucket'),
        ForeignKeyConstraint(['company_id','compensation_id'],['payroll_leave_compensations.company_id','payroll_leave_compensations.id'],name='fk_plcl_compensation',ondelete='RESTRICT'),
        ForeignKeyConstraint(['company_id','account_id'],['payroll_leave_accounts.company_id','payroll_leave_accounts.id'],name='fk_plcl_account',ondelete='RESTRICT'),
        ForeignKeyConstraint(['company_id','balance_entry_id'],['payroll_leave_balance_entries.company_id','payroll_leave_balance_entries.id'],name='fk_plcl_balance',ondelete='RESTRICT'),
        UniqueConstraint('company_id','balance_entry_id',name='uq_plcl_balance'),
        CheckConstraint("acquisition_bucket IN ('before_2024','from_2024')",name='ck_plcl_bucket'),
        CheckConstraint("consumed_days > 0 AND compensated_days >= 0 AND amount >= 0 AND consumed_days <> 'NaN'::numeric AND compensated_days <> 'NaN'::numeric AND amount <> 'NaN'::numeric",name='ck_plcl_amounts'),
    )
    id: Mapped[int]=mapped_column(primary_key=True)
    company_id: Mapped[int]=mapped_column(Integer,nullable=False)
    compensation_id: Mapped[int]=mapped_column(Integer,nullable=False)
    account_id: Mapped[int]=mapped_column(Integer,nullable=False)
    balance_entry_id: Mapped[int]=mapped_column(Integer,nullable=False)
    acquisition_bucket: Mapped[str]=mapped_column(String(16),nullable=False)
    consumed_days: Mapped[Decimal]=mapped_column(Numeric(16,8),nullable=False)
    compensated_days: Mapped[Decimal]=mapped_column(Numeric(10,2),nullable=False)
    amount: Mapped[Decimal]=mapped_column(Numeric(18,2),nullable=False)
    basis_snapshot: Mapped[dict]=mapped_column(JSON,nullable=False)


class PayrollLeaveCompensationReversal(Base):
    __tablename__ = 'payroll_leave_compensation_reversals'
    __table_args__ = (
        UniqueConstraint('company_id', 'compensation_id', name='uq_plcr_source'),
        UniqueConstraint('company_id', 'request_key', name='uq_plcr_request'),
        ForeignKeyConstraint(['company_id', 'compensation_id'],
            ['payroll_leave_compensations.company_id', 'payroll_leave_compensations.id'],
            name='fk_plcr_compensation', ondelete='RESTRICT'),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(Integer, nullable=False)
    compensation_id: Mapped[int] = mapped_column(Integer, nullable=False)
    effective_date: Mapped[date] = mapped_column(Date, nullable=False)
    source_reference: Mapped[str] = mapped_column(String(1000), nullable=False)
    request_key: Mapped[str] = mapped_column(String(100), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id', ondelete='RESTRICT'), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
