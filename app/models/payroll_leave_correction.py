"""Immutable source evidence for applied leave financial corrections."""
from sqlalchemy import (BigInteger, text, CheckConstraint, ForeignKeyConstraint, Integer, JSON,
                        String, UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


class PayrollLeaveCorrection(Base):
    __tablename__ = 'payroll_leave_corrections'
    __table_args__ = (
        UniqueConstraint('company_id','id',name='uq_leave_correction_company'),
        UniqueConstraint('company_id','application_id',name='uq_leave_correction_app'),
        UniqueConstraint('company_id','request_id',name='uq_leave_correction_request'),
        ForeignKeyConstraint(['company_id','request_id'],['payroll_leave_correction_requests.company_id','payroll_leave_correction_requests.id'],name='fk_leave_correction_request',ondelete='RESTRICT'),
        UniqueConstraint('company_id','target_kind','target_id','revision',name='uq_leave_correction_revision'),
        UniqueConstraint('company_id','supersedes_id',name='uq_leave_correction_parent'),
        ForeignKeyConstraint(['company_id','application_id'],
            ['payroll_sick_correction_applications.company_id','payroll_sick_correction_applications.id'],
            name='fk_leave_correction_app',ondelete='RESTRICT'),
        ForeignKeyConstraint(['company_id','supersedes_id'],
            ['payroll_leave_corrections.company_id','payroll_leave_corrections.id'],
            name='fk_leave_correction_parent',ondelete='RESTRICT'),
        CheckConstraint("target_kind IN ('vacation','compensation')",name='ck_leave_correction_kind'),
        CheckConstraint("revision > 0 AND ((revision=1 AND supersedes_id IS NULL) OR (revision>1 AND supersedes_id IS NOT NULL))",name='ck_leave_correction_revision'),
        CheckConstraint('target_id > 0',name='ck_leave_correction_target'),
    )
    id: Mapped[int] = mapped_column(Integer,primary_key=True)
    company_id: Mapped[int] = mapped_column(Integer,nullable=False)
    request_id: Mapped[int] = mapped_column(Integer,nullable=False)
    application_id: Mapped[int] = mapped_column(Integer,nullable=False)
    target_kind: Mapped[str] = mapped_column(String(20),nullable=False)
    target_id: Mapped[int] = mapped_column(Integer,nullable=False)
    revision: Mapped[int] = mapped_column(Integer,nullable=False)
    supersedes_id: Mapped[int | None] = mapped_column(Integer)
    creation_xid: Mapped[int] = mapped_column(BigInteger,nullable=False,server_default=text('txid_current()'))
    reviewed_by: Mapped[int] = mapped_column(Integer,nullable=False)
    original_snapshot: Mapped[dict] = mapped_column(JSON,nullable=False)
    replacement_snapshot: Mapped[dict] = mapped_column(JSON,nullable=False)


class PayrollLeaveCorrectionRequest(Base):
    __tablename__ = 'payroll_leave_correction_requests'
    __table_args__ = (
        UniqueConstraint('company_id','id',name='uq_leave_request_company'),
        UniqueConstraint('company_id','request_key',name='uq_leave_request_key'),
    )
    id: Mapped[int] = mapped_column(Integer,primary_key=True)
    company_id: Mapped[int] = mapped_column(Integer,nullable=False)
    request_key: Mapped[str] = mapped_column(String(100),nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64),nullable=False)
    created_by: Mapped[int] = mapped_column(Integer,nullable=False)
    payload: Mapped[dict] = mapped_column(JSON,nullable=False)


class PayrollLeaveDayCorrection(Base):
    __tablename__='payroll_leave_day_corrections'
    __table_args__=(
        UniqueConstraint('company_id','correction_id','allocation_id',name='uq_leave_day_correction_allocation'),
        ForeignKeyConstraint(['company_id','correction_id'],['payroll_leave_corrections.company_id','payroll_leave_corrections.id'],name='fk_leave_day_correction',ondelete='RESTRICT'),
        ForeignKeyConstraint(['company_id','allocation_id'],['payroll_leave_balance_entries.company_id','payroll_leave_balance_entries.id'],name='fk_leave_day_allocation',ondelete='RESTRICT'),
        CheckConstraint('days > 0',name='ck_leave_day_positive'),
    )
    id: Mapped[int]=mapped_column(Integer,primary_key=True)
    company_id: Mapped[int]=mapped_column(Integer,nullable=False)
    correction_id: Mapped[int]=mapped_column(Integer,nullable=False)
    allocation_id: Mapped[int]=mapped_column(Integer,nullable=False)
    days: Mapped[int]=mapped_column(Integer,nullable=False)


class PayrollLeaveTransfer(Base):
    __tablename__='payroll_leave_transfers'
    __table_args__=(
        UniqueConstraint('company_id','leave_request_id',name='uq_leave_transfer_request'),
        ForeignKeyConstraint(['company_id','correction_id'],['payroll_leave_corrections.company_id','payroll_leave_corrections.id'],name='fk_leave_transfer_correction',ondelete='RESTRICT'),
        ForeignKeyConstraint(['company_id','leave_request_id'],['leave_requests.company_id','leave_requests.id'],name='fk_leave_transfer_request',ondelete='RESTRICT'),
    )
    id: Mapped[int]=mapped_column(Integer,primary_key=True)
    company_id: Mapped[int]=mapped_column(Integer,nullable=False)
    correction_id: Mapped[int]=mapped_column(Integer,nullable=False)
    leave_request_id: Mapped[int]=mapped_column(Integer,nullable=False)


class PayrollLeaveCompensationExtraSource(Base):
    """Immutable identity for a right bucket absent from the original settlement.

    It contains no money and consumes no days. Only an applied reviewed snapshot
    can give this source an earning or a day movement.
    """
    __tablename__='payroll_leave_compensation_extra_sources'
    __table_args__=(
        UniqueConstraint('company_id','id',name='uq_leave_extra_company'),
        UniqueConstraint('company_id','compensation_id','account_id','acquisition_bucket',name='uq_leave_extra_bucket'),
        ForeignKeyConstraint(['company_id','compensation_id'],['payroll_leave_compensations.company_id','payroll_leave_compensations.id'],name='fk_leave_extra_compensation',ondelete='RESTRICT'),
        ForeignKeyConstraint(['company_id','account_id'],['payroll_leave_accounts.company_id','payroll_leave_accounts.id'],name='fk_leave_extra_account',ondelete='RESTRICT'),
        CheckConstraint("acquisition_bucket IN ('before_2024','from_2024')",name='ck_leave_extra_bucket'),
    )
    id: Mapped[int]=mapped_column(Integer,primary_key=True)
    company_id: Mapped[int]=mapped_column(Integer,nullable=False)
    compensation_id: Mapped[int]=mapped_column(Integer,nullable=False)
    account_id: Mapped[int]=mapped_column(Integer,nullable=False)
    acquisition_bucket: Mapped[str]=mapped_column(String(16),nullable=False)
