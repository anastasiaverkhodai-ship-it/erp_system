"""Documented leave accounts and append-only changes to their day balances."""
from datetime import date, datetime
from decimal import Decimal
from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, ForeignKeyConstraint, Integer, JSON, Numeric, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


class PayrollLeaveAccount(Base):
    __tablename__ = 'payroll_leave_accounts'
    __table_args__ = (
        UniqueConstraint('company_id','id',name='uq_pla_company_id'),
        UniqueConstraint('company_id','employment_contract_id','leave_kind','entitlement_code','period_start',name='uq_pla_entitlement'),
        UniqueConstraint('company_id','request_key',name='uq_pla_request'),
        ForeignKeyConstraint(['company_id','employment_contract_id'],['employment_contracts.company_id','employment_contracts.id'],name='fk_pla_contract',ondelete='RESTRICT'),
        ForeignKeyConstraint(['company_id','opening_id'],['payroll_leave_openings.company_id','payroll_leave_openings.id'],name='fk_pla_opening',ondelete='RESTRICT'),
        UniqueConstraint('company_id','opening_id',name='uq_pla_opening'),
        CheckConstraint("leave_kind IN ('annual','additional','social')",name='ck_pla_kind'),
        CheckConstraint('period_end >= period_start AND accrual_start >= period_start AND accrual_start <= period_end + 1',name='ck_pla_dates'),
        CheckConstraint("length(trim(source_reference)) > 0 AND length(trim(entitlement_code)) > 0",name='ck_pla_evidence'),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(Integer,nullable=False)
    employment_contract_id: Mapped[int] = mapped_column(Integer,nullable=False)
    leave_kind: Mapped[str] = mapped_column(String(10),nullable=False)
    entitlement_code: Mapped[str] = mapped_column(String(80),nullable=False)
    period_start: Mapped[date] = mapped_column(Date,nullable=False)
    period_end: Mapped[date] = mapped_column(Date,nullable=False)
    accrual_start: Mapped[date] = mapped_column(Date,nullable=False)
    opening_id: Mapped[int | None] = mapped_column(Integer)
    # Exact dated rights and calendar facts, not a manually supplied total.
    basis: Mapped[dict] = mapped_column(JSON,nullable=False)
    source_reference: Mapped[str] = mapped_column(String(1000),nullable=False)
    request_key: Mapped[str] = mapped_column(String(100),nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64),nullable=False)
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id',ondelete='RESTRICT'),nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),server_default=func.now(),nullable=False)


class PayrollLeaveBalanceEntry(Base):
    __tablename__ = 'payroll_leave_balance_entries'
    __table_args__ = (
        UniqueConstraint('company_id','id',name='uq_plbe_company_id'),
        UniqueConstraint('company_id','request_key','line_no',name='uq_plbe_request_line'),
        UniqueConstraint('company_id','reversal_of_id',name='uq_plbe_reversal'),
        ForeignKeyConstraint(['company_id','account_id'],['payroll_leave_accounts.company_id','payroll_leave_accounts.id'],name='fk_plbe_account',ondelete='RESTRICT'),
        ForeignKeyConstraint(['company_id','leave_request_id'],['leave_requests.company_id','leave_requests.id'],name='fk_plbe_leave',ondelete='RESTRICT'),
        ForeignKeyConstraint(['company_id','termination_event_id'],['employment_events.company_id','employment_events.id'],name='fk_plbe_termination',ondelete='RESTRICT'),
        ForeignKeyConstraint(['company_id','reversal_of_id'],['payroll_leave_balance_entries.company_id','payroll_leave_balance_entries.id'],name='fk_plbe_reversal',ondelete='RESTRICT'),
        CheckConstraint("kind IN ('opening','accrual','use','compensation','reversal')",name='ck_plbe_kind'),
        CheckConstraint("acquisition_bucket IN ('before_2024','from_2024')",name='ck_plbe_bucket'),
        CheckConstraint("days <> 0 AND days <> 'NaN'::numeric",name='ck_plbe_days'),
        CheckConstraint("(kind IN ('opening','accrual') AND days > 0) OR (kind IN ('use','compensation') AND days < 0) OR kind='reversal'",name='ck_plbe_sign'),
        CheckConstraint("(kind='reversal') = (reversal_of_id IS NOT NULL)",name='ck_plbe_reversal_source'),
        CheckConstraint("kind <> 'use' OR leave_request_id IS NOT NULL",name='ck_plbe_use_source'),
        CheckConstraint("kind <> 'compensation' OR termination_event_id IS NOT NULL",name='ck_plbe_comp_source'),
        CheckConstraint('line_no > 0',name='ck_plbe_line'),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(Integer,nullable=False)
    account_id: Mapped[int] = mapped_column(Integer,nullable=False)
    kind: Mapped[str] = mapped_column(String(20),nullable=False)
    effective_date: Mapped[date] = mapped_column(Date,nullable=False)
    acquisition_bucket: Mapped[str] = mapped_column(String(16),nullable=False)
    days: Mapped[Decimal] = mapped_column(Numeric(16,8),nullable=False)
    leave_request_id: Mapped[int | None] = mapped_column(Integer)
    termination_event_id: Mapped[int | None] = mapped_column(Integer)
    reversal_of_id: Mapped[int | None] = mapped_column(Integer)
    source_reference: Mapped[str] = mapped_column(String(1000),nullable=False)
    request_key: Mapped[str] = mapped_column(String(100),nullable=False)
    line_no: Mapped[int] = mapped_column(Integer,nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64),nullable=False)
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id',ondelete='RESTRICT'),nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),server_default=func.now(),nullable=False)


class PayrollLeaveAccrual(Base):
    __tablename__ = 'payroll_leave_accruals'
    __table_args__ = (
        UniqueConstraint('company_id','id',name='uq_plac_company_id'),
        UniqueConstraint('company_id','request_key',name='uq_plac_request'),
        ForeignKeyConstraint(['company_id','account_id'],['payroll_leave_accounts.company_id','payroll_leave_accounts.id'],name='fk_plac_account',ondelete='RESTRICT'),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(Integer,nullable=False)
    account_id: Mapped[int] = mapped_column(Integer,nullable=False)
    through: Mapped[date] = mapped_column(Date,nullable=False)
    source_snapshot: Mapped[dict] = mapped_column(JSON,nullable=False)
    request_key: Mapped[str] = mapped_column(String(100),nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64),nullable=False)
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id',ondelete='RESTRICT'),nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),server_default=func.now(),nullable=False)
