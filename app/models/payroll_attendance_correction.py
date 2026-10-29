"""Append-only reviewed changes to attendance after payroll finalization."""
from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, ForeignKeyConstraint, Integer, JSON, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


class PayrollAttendanceCorrectionRequest(Base):
    __tablename__ = 'payroll_attendance_correction_requests'
    __table_args__ = (
        UniqueConstraint('company_id', 'id', name='uq_attendance_request_company'),
        UniqueConstraint('company_id', 'request_key', name='uq_attendance_request_key'),
        ForeignKeyConstraint(['company_id','calculation_id'], ['payroll_calculations.company_id','payroll_calculations.id'], name='fk_attendance_request_calculation'),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey('companies.id'), nullable=False)
    calculation_id: Mapped[int] = mapped_column(Integer, nullable=False)
    request_key: Mapped[str] = mapped_column(String(90), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id'), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class PayrollAttendanceCorrection(Base):
    __tablename__ = 'payroll_attendance_corrections'
    __table_args__ = (
        UniqueConstraint('company_id', 'id', name='uq_attendance_correction_company'),
        UniqueConstraint('company_id', 'request_id', name='uq_attendance_correction_request'),
        UniqueConstraint('company_id', 'application_id', name='uq_attendance_correction_application'),
        ForeignKeyConstraint(['company_id','request_id'], ['payroll_attendance_correction_requests.company_id','payroll_attendance_correction_requests.id'], name='fk_attendance_correction_request'),
        ForeignKeyConstraint(['company_id','application_id'], ['payroll_sick_correction_applications.company_id','payroll_sick_correction_applications.id'], name='fk_attendance_correction_application'),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey('companies.id'), nullable=False)
    request_id: Mapped[int] = mapped_column(Integer, nullable=False)
    application_id: Mapped[int] = mapped_column(Integer, nullable=False)
    reviewed_by: Mapped[int] = mapped_column(ForeignKey('users.id'), nullable=False)
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
