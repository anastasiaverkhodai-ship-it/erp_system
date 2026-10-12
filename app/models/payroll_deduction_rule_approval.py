"""Persisted legal-rule approval state for payroll deductions.

Persistence does not authorize automatic payroll withholding.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class PayrollDeductionRuleApproval(Base):
    __tablename__ = "payroll_deduction_rule_approvals"

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "id",
            name="uq_pdra_company_id_id",
        ),
        UniqueConstraint(
            "company_id",
            "legal_category",
            "rule_version",
            name="uq_pdra_company_category_version",
        ),
        CheckConstraint(
            "legal_category IN ("
            "'employer_statutory', "
            "'enforcement_other', "
            "'enforcement_special', "
            "'minor_child_alimony')",
            name="ck_pdra_category",
        ),
        CheckConstraint(
            "status IN ('pending', 'approved', 'revoked')",
            name="ck_pdra_status",
        ),
        CheckConstraint(
            "char_length(trim(rule_version)) > 0",
            name="ck_pdra_version",
        ),
        CheckConstraint(
            "char_length(trim(legal_reference)) > 0",
            name="ck_pdra_reference",
        ),
        CheckConstraint(
            "valid_to IS NULL OR valid_to >= valid_from",
            name="ck_pdra_dates",
        ),
        CheckConstraint(
            "(status = 'pending' "
            "AND approved_by IS NULL AND approved_at IS NULL "
            "AND revoked_by IS NULL AND revoked_at IS NULL) "
            "OR (status = 'approved' "
            "AND approved_by IS NOT NULL AND approved_at IS NOT NULL "
            "AND revoked_by IS NULL AND revoked_at IS NULL) "
            "OR (status = 'revoked' "
            "AND approved_by IS NOT NULL AND approved_at IS NOT NULL "
            "AND revoked_by IS NOT NULL AND revoked_at IS NOT NULL)",
            name="ck_pdra_lifecycle",
        ),
        CheckConstraint(
            "approved_by IS NULL OR approved_by <> created_by",
            name="ck_pdra_four_eyes",
        ),
        CheckConstraint(
            "revoked_at IS NULL OR revoked_at >= approved_at",
            name="ck_pdra_revocation_order",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        Identity(),
        primary_key=True,
    )

    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="RESTRICT"),
        nullable=False,
    )

    legal_category: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    rule_version: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    legal_reference: Mapped[str] = mapped_column(
        String(500),
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

    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="pending",
    )

    created_by: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    approved_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )

    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    revoked_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )

    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
