"""Immutable application of a sick correction and its payroll differences."""
from datetime import date, datetime
from decimal import Decimal
from sqlalchemy import (CheckConstraint, Date, DateTime, ForeignKeyConstraint, Integer, JSON, Numeric,
                        String, Text, UniqueConstraint, func, text)
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


class PayrollSickCorrectionApplication(Base):
    __tablename__ = 'payroll_sick_correction_applications'
    __table_args__ = (
        CheckConstraint("(source_kind='sick' AND original_sick_id IS NOT NULL AND replacement_sick_id IS NOT NULL) OR (source_kind IN ('leave','attendance') AND original_sick_id IS NULL AND replacement_sick_id IS NULL)", name='ck_payroll_correction_source_kind'),
        UniqueConstraint('company_id', 'id', name='uq_sick_application_company_id'),
        UniqueConstraint('company_id', 'request_key', name='uq_sick_application_request'),
        UniqueConstraint('company_id', 'original_sick_id', name='uq_sick_application_original'),
        UniqueConstraint('company_id', 'replacement_sick_id', name='uq_sick_application_replacement'),
        ForeignKeyConstraint(['company_id','original_sick_id'], ['payroll_sick_leave_calculations.company_id','payroll_sick_leave_calculations.id'], name='fk_sick_application_original', ondelete='RESTRICT'),
        ForeignKeyConstraint(['company_id','replacement_sick_id'], ['payroll_sick_leave_calculations.company_id','payroll_sick_leave_calculations.id'], name='fk_sick_application_replacement', ondelete='RESTRICT'),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(Integer, nullable=False)
    source_kind: Mapped[str] = mapped_column(String(10), nullable=False, default="sick", server_default="sick")
    original_sick_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    replacement_sick_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    payroll_calculation_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list, server_default=text("'[]'::json"))
    source_replacements: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict, server_default=text("'{}'::json"))
    request_key: Mapped[str] = mapped_column(String(100), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    correction_date: Mapped[date] = mapped_column(Date, nullable=False)
    created_by: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class PayrollSickPayrollRevision(Base):
    __tablename__ = 'payroll_sick_payroll_revisions'
    __table_args__ = (
        UniqueConstraint('company_id','id', name='uq_sick_payroll_revision_company_id'),
        UniqueConstraint('company_id','original_calculation_id', name='uq_sick_payroll_revision_original'),
        UniqueConstraint('company_id','replacement_calculation_id', name='uq_sick_payroll_revision_replacement'),
        UniqueConstraint('company_id','journal_entry_id', name='uq_sick_payroll_revision_journal'),
        ForeignKeyConstraint(['company_id','application_id'], ['payroll_sick_correction_applications.company_id','payroll_sick_correction_applications.id'], name='fk_sick_payroll_revision_application', ondelete='RESTRICT'),
        ForeignKeyConstraint(['company_id','original_calculation_id'], ['payroll_calculations.company_id','payroll_calculations.id'], name='fk_sick_payroll_revision_original', ondelete='RESTRICT'),
        ForeignKeyConstraint(['company_id','replacement_calculation_id'], ['payroll_calculations.company_id','payroll_calculations.id'], name='fk_sick_payroll_revision_replacement', ondelete='RESTRICT'),
        ForeignKeyConstraint(['company_id','journal_entry_id'], ['journal_entries.company_id','journal_entries.id'], name='fk_sick_payroll_revision_journal', ondelete='RESTRICT'),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(Integer, nullable=False)
    application_id: Mapped[int] = mapped_column(Integer, nullable=False)
    original_calculation_id: Mapped[int] = mapped_column(Integer, nullable=False)
    replacement_calculation_id: Mapped[int] = mapped_column(Integer, nullable=False)
    journal_entry_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    original_financial: Mapped[dict] = mapped_column(JSON, nullable=False)
    replacement_financial: Mapped[dict] = mapped_column(JSON, nullable=False)
    net_delta: Mapped[Decimal] = mapped_column(Numeric(18,2), nullable=False)
