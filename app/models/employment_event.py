"""Documented, immutable changes to employment terms."""
from datetime import date, datetime
from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, ForeignKeyConstraint, Index, Integer, JSON, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


class EmploymentEvent(Base):
    __tablename__ = 'employment_events'
    __table_args__ = (
        UniqueConstraint('company_id', 'id', name='uq_employment_events_company_id'),
        UniqueConstraint('company_id', 'request_key', name='uq_employment_events_request'),
        UniqueConstraint('reversal_of_id', name='uq_employment_events_reversal'),
        ForeignKeyConstraint(['company_id','employment_contract_id'],
            ['employment_contracts.company_id','employment_contracts.id'], name='fk_employment_events_contract', ondelete='RESTRICT'),
        ForeignKeyConstraint(['company_id','reversal_of_id'],
            ['employment_events.company_id','employment_events.id'], name='fk_employment_events_reversal', ondelete='RESTRICT'),
        CheckConstraint("event_type IN ('hire','transfer','termination','reversal')", name='ck_employment_events_type'),
        CheckConstraint("(event_type = 'reversal') = (reversal_of_id IS NOT NULL)", name='ck_employment_events_reversal'),
        CheckConstraint("length(trim(order_number)) > 0 AND length(trim(reason)) > 0 AND length(trim(request_key)) > 0", name='ck_employment_events_evidence'),
        Index('ix_employment_events_timeline','company_id','employment_contract_id','effective_date','id'),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey('companies.id',ondelete='RESTRICT'),nullable=False)
    employment_contract_id: Mapped[int] = mapped_column(Integer,nullable=False)
    event_type: Mapped[str] = mapped_column(String(20),nullable=False)
    effective_date: Mapped[date] = mapped_column(Date,nullable=False)
    order_number: Mapped[str] = mapped_column(String(100),nullable=False)
    order_date: Mapped[date] = mapped_column(Date,nullable=False)
    reason: Mapped[str] = mapped_column(String(1000),nullable=False)
    request_key: Mapped[str] = mapped_column(String(200),nullable=False)
    before_state: Mapped[dict] = mapped_column(JSON,nullable=False)
    after_state: Mapped[dict] = mapped_column(JSON,nullable=False)
    request_payload: Mapped[dict] = mapped_column(JSON,nullable=False)
    reversal_of_id: Mapped[int | None] = mapped_column(Integer,nullable=True)
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id',ondelete='RESTRICT'),nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),server_default=func.now(),nullable=False)
