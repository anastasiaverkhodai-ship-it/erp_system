"""Append-only, review-required sick-pay correction requests."""
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    DateTime, ForeignKeyConstraint, Integer, Numeric,
    String, Text, UniqueConstraint, CheckConstraint, func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class PayrollSickCorrection(Base):
    __tablename__ = "payroll_sick_corrections"

    __table_args__ = (
        ForeignKeyConstraint(
            ["company_id", "original_calculation_id"],
            [
                "payroll_sick_leave_calculations.company_id",
                "payroll_sick_leave_calculations.id",
            ],
            name="fk_sick_correction_original",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "insurance_evidence_id"],
            [
                "payroll_insurance_evidence.company_id",
                "payroll_insurance_evidence.id",
            ],
            name="fk_sick_correction_evidence",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "company_id", "request_key",
            name="uq_sick_correction_request",
        ),
        UniqueConstraint(
            "company_id", "original_calculation_id",
            name="uq_sick_correction_original",
        ),
        CheckConstraint(
            "status IN ('pending_review', 'applied')",
            name="ck_sick_correction_pending",
        ),
        CheckConstraint(
            "original_amount >= 0",
            name="ck_sick_correction_original_amount",
        ),
        CheckConstraint(
            "length(trim(reason)) >= 10",
            name="ck_sick_correction_reason",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True
    )
    company_id: Mapped[int] = mapped_column(
        Integer, nullable=False
    )
    original_calculation_id: Mapped[int] = mapped_column(
        Integer, nullable=False
    )
    insurance_evidence_id: Mapped[int] = mapped_column(
        Integer, nullable=False
    )
    request_key: Mapped[str] = mapped_column(
        String(100), nullable=False
    )
    reason: Mapped[str] = mapped_column(
        Text, nullable=False
    )
    original_amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 2), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="pending_review"
    )
    requested_by: Mapped[int] = mapped_column(
        Integer, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
