"""Reviewed insurance-service evidence for a particular incapacity case."""
from datetime import date, datetime
from decimal import Decimal
from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, ForeignKeyConstraint, Integer, Numeric, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


class PayrollInsuranceEvidence(Base):
    __tablename__ = 'payroll_insurance_evidence'
    __table_args__ = (
        UniqueConstraint('company_id', 'id', name='uq_payroll_insurance_evidence_company'),
        UniqueConstraint('company_id', 'request_key', name='uq_payroll_insurance_evidence_request'),
        ForeignKeyConstraint(['company_id', 'employee_id'], ['employees.company_id', 'employees.id'],
                             name='fk_payroll_insurance_evidence_employee', ondelete='RESTRICT'),
        CheckConstraint('total_service_months >= 0 AND service_days BETWEEN 0 AND 29', name='ck_payroll_insurance_service'),
        CheckConstraint('recent_service_months >= 0 AND recent_service_months <= 12 AND recent_service_months <= total_service_months + service_days / 30.0', name='ck_payroll_insurance_recent_service'),
        CheckConstraint("entitlement IN ('standard','chornobyl_1_3','veteran','rehabilitated')", name='ck_payroll_insurance_entitlement'),
        CheckConstraint("entitlement = 'standard' OR (entitlement_reference IS NOT NULL AND length(trim(entitlement_reference)) > 0)", name='ck_payroll_insurance_entitlement_ref'),
        CheckConstraint("length(trim(document_reference)) > 0 AND length(trim(request_key)) > 0", name='ck_payroll_insurance_reference'),
        CheckConstraint("status IN ('pending','verified','rejected')", name='ck_payroll_insurance_status'),
        CheckConstraint("(status = 'pending' AND reviewed_by IS NULL AND reviewed_at IS NULL) OR (status IN ('verified','rejected') AND reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL AND reviewed_by <> created_by)", name='ck_payroll_insurance_review'),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(Integer, nullable=False)
    employee_id: Mapped[int] = mapped_column(Integer, nullable=False)
    case_start: Mapped[date] = mapped_column(Date, nullable=False)
    total_service_months: Mapped[int] = mapped_column(Integer, nullable=False)
    service_days: Mapped[int] = mapped_column(Integer, nullable=False)
    recent_service_months: Mapped[Decimal] = mapped_column(Numeric(6,3), nullable=False)
    entitlement: Mapped[str] = mapped_column(String(30), nullable=False)
    entitlement_reference: Mapped[str | None] = mapped_column(String(500), nullable=True)
    document_reference: Mapped[str] = mapped_column(String(500), nullable=False)
    issued_on: Mapped[date] = mapped_column(Date, nullable=False)
    request_key: Mapped[str] = mapped_column(String(200), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default='pending', server_default='pending')
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id', ondelete='RESTRICT'), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey('users.id', ondelete='RESTRICT'), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
