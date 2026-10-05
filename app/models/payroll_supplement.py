"""Documented earning supplements attached to an open payroll input."""
from datetime import date, datetime
from decimal import Decimal
from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, ForeignKeyConstraint, Integer, Numeric, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped,mapped_column
from app.core.database import Base


class PayrollSupplement(Base):
    __tablename__='payroll_supplements'
    __table_args__=(
        UniqueConstraint('company_id','id',name='uq_payroll_supplement_company_id'),
        UniqueConstraint('company_id','request_key',name='uq_payroll_supplement_request'),
        ForeignKeyConstraint(['company_id','payroll_input_id'],['payroll_inputs.company_id','payroll_inputs.id'],name='fk_payroll_supplement_input',ondelete='RESTRICT'),
        CheckConstraint("kind IN ('bonus','holiday_bonus','hardship','night','overtime','rest_day','regular_extra')",name='ck_payroll_supplement_kind'),
        CheckConstraint("minutes >= 0 AND coefficient >= 0 AND amount >= 0",name='ck_payroll_supplement_values'),
        CheckConstraint("(cancelled_at IS NULL) = (cancelled_by IS NULL)",name='ck_payroll_supplement_cancel'),
    )
    id: Mapped[int]=mapped_column(primary_key=True)
    company_id: Mapped[int]=mapped_column(Integer,nullable=False)
    payroll_input_id: Mapped[int]=mapped_column(Integer,nullable=False)
    kind: Mapped[str]=mapped_column(String(20),nullable=False)
    work_date: Mapped[date]=mapped_column(Date,nullable=False)
    minutes: Mapped[int]=mapped_column(Integer,nullable=False)
    coefficient: Mapped[Decimal]=mapped_column(Numeric(10,6),nullable=False)
    amount: Mapped[Decimal]=mapped_column(Numeric(18,2),nullable=False)
    source_reference: Mapped[str]=mapped_column(String(500),nullable=False)
    request_key: Mapped[str]=mapped_column(String(200),nullable=False)
    request_fingerprint: Mapped[str]=mapped_column(String(64),nullable=False)
    cancelled_at: Mapped[datetime | None]=mapped_column(DateTime(timezone=True),nullable=True)
    cancelled_by: Mapped[int | None]=mapped_column(ForeignKey('users.id',ondelete='RESTRICT'),nullable=True)
    created_by: Mapped[int]=mapped_column(ForeignKey('users.id',ondelete='RESTRICT'),nullable=False)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),server_default=func.now(),nullable=False)
