
"""Append-only, documented professional evidence for D5 preparation.

Stored evidence is not automatically a certified profession classifier
entry or an approved statutory D5 row.
"""
from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint, Date, DateTime, ForeignKeyConstraint,
    Integer, String, UniqueConstraint, func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class PayrollD5ProfessionalEvidence(Base):
    __tablename__ = "payroll_d5_professional_evidence"

    __table_args__ = (
        UniqueConstraint(
            "company_id", "id",
            name="uq_d5_prof_evidence_company_id",
        ),
        UniqueConstraint(
            "company_id", "request_key",
            name="uq_d5_prof_evidence_request",
        ),
        ForeignKeyConstraint(
            ["company_id", "employment_contract_id"],
            ["employment_contracts.company_id", "employment_contracts.id"],
            name="fk_d5_prof_evidence_contract",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "employment_event_id"],
            ["employment_events.company_id", "employment_events.id"],
            name="fk_d5_prof_evidence_event",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "position_id"],
            ["positions.company_id", "positions.id"],
            name="fk_d5_prof_evidence_position",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "company_id > 0 AND employee_id > 0 "
            "AND employment_contract_id > 0 "
            "AND employment_event_id > 0 AND position_id > 0",
            name="ck_d5_prof_evidence_positive_ids",
        ),
        CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="ck_d5_prof_evidence_dates",
        ),
        CheckConstraint(
            "employment_category IN "
            "('primary','internal_secondary','external_secondary')",
            name="ck_d5_prof_evidence_category",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(Integer, nullable=False)
    employee_id: Mapped[int] = mapped_column(Integer, nullable=False)
    employment_contract_id: Mapped[int] = mapped_column(Integer, nullable=False)
    employment_event_id: Mapped[int] = mapped_column(Integer, nullable=False)
    position_id: Mapped[int] = mapped_column(Integer, nullable=False)

    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date)
    documented_on: Mapped[date] = mapped_column(Date, nullable=False)

    professional_name: Mapped[str] = mapped_column(String(250), nullable=False)
    profession_code: Mapped[str] = mapped_column(String(30), nullable=False)
    position_name: Mapped[str] = mapped_column(String(250), nullable=False)
    employment_category: Mapped[str] = mapped_column(String(30), nullable=False)
    evidence_reference: Mapped[str] = mapped_column(String(500), nullable=False)
    request_key: Mapped[str] = mapped_column(String(200), nullable=False)
    created_by: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
