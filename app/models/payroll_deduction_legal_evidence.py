"""Persisted evidence for payroll deduction legal classification."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Identity,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class PayrollDeductionLegalEvidence(Base):
    __tablename__ = "payroll_deduction_legal_evidence"

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "id",
            name="uq_pdle_company_id_id",
        ),
        UniqueConstraint(
            "company_id",
            "payroll_deduction_instruction_id",
            name="uq_pdle_instruction",
        ),
        ForeignKeyConstraint(
            [
                "company_id",
                "payroll_deduction_instruction_id",
            ],
            [
                "payroll_deduction_instructions.company_id",
                "payroll_deduction_instructions.id",
            ],
            name="fk_pdle_instruction",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "legal_category IN ("
            "'employer_statutory', "
            "'enforcement_other', "
            "'enforcement_special', "
            "'minor_child_alimony'"
            ")",
            name="ck_pdle_category",
        ),
        CheckConstraint(
            "char_length(trim(document_reference)) > 0",
            name="ck_pdle_document",
        ),
        CheckConstraint(
            "verification_status IN ('pending', 'verified', 'rejected')",
            name="ck_pdle_status",
        ),
        CheckConstraint(
            "(verification_status = 'verified' "
            "AND verified_by IS NOT NULL "
            "AND verified_at IS NOT NULL) "
            "OR verification_status <> 'verified'",
            name="ck_pdle_verified",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        Identity(),
        primary_key=True,
    )

    company_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    payroll_deduction_instruction_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    legal_category: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    document_reference: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    verification_status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="pending",
    )

    verified_by: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    created_by: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
