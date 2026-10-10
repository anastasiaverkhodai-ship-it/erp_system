from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class PayrollEmployeeTaxEvidence(Base):
    """
    Documentary evidence supporting employee-specific tax treatment.

    Evidence does not itself grant a tax benefit or special rate.
    Eligibility must be validated against the applicable tax rule.
    """

    __tablename__ = "payroll_employee_tax_evidence"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    company_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    employee_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    document_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    document_number: Mapped[str] = mapped_column(
        String(150),
        nullable=False,
    )

    issued_on: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )

    valid_from: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )

    valid_to: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )

    entitlement_type: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )

    entitlement_code: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    verification_status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="pending",
        server_default="pending",
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
        server_default=func.now(),
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["company_id", "employee_id"],
            ["employees.company_id", "employees.id"],
            name="fk_payroll_tax_evidence_employee",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name="fk_payroll_tax_evidence_created_by",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["verified_by"],
            ["users.id"],
            name="fk_payroll_tax_evidence_verified_by",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "company_id",
            "id",
            name="uq_payroll_tax_evidence_company_id_id",
        ),
        CheckConstraint(
            "valid_to IS NULL OR valid_to >= valid_from",
            name="ck_payroll_tax_evidence_valid_range",
        ),
        CheckConstraint(
            "issued_on <= valid_from",
            name="ck_payroll_tax_evidence_issue_date",
        ),
        CheckConstraint(
            "entitlement_type IN "
            "('benefit','exemption','individual_rate')",
            name="ck_payroll_tax_evidence_entitlement_type",
        ),
        CheckConstraint(
            "verification_status IN "
            "('pending','verified','rejected')",
            name="ck_payroll_tax_evidence_status",
        ),
        CheckConstraint(
            "("
            "verification_status = 'pending' "
            "AND verified_by IS NULL "
            "AND verified_at IS NULL"
            ") OR ("
            "verification_status IN ('verified', 'rejected') "
            "AND verified_by IS NOT NULL "
            "AND verified_at IS NOT NULL"
            ")",
            name="ck_payroll_tax_evidence_verification",
        ),
        CheckConstraint(
            "length(trim(document_type)) > 0 "
            "AND length(trim(document_number)) > 0 "
            "AND length(trim(entitlement_code)) > 0",
            name="ck_payroll_tax_evidence_nonempty",
        ),
        Index(
            "ix_payroll_tax_evidence_employee_valid",
            "company_id",
            "employee_id",
            "valid_from",
        ),
        Index(
            "ix_payroll_tax_evidence_entitlement",
            "company_id",
            "entitlement_type",
            "entitlement_code",
        ),
    )
