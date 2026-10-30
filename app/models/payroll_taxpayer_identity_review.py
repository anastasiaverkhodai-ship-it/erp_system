"""Reviewed taxpayer identification evidence.

Not itself an approval of a statutory form or income code.
"""
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


class PayrollTaxpayerIdentityReview(Base):
    __tablename__ = "payroll_taxpayer_identity_reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(Integer, nullable=False)
    employee_id: Mapped[int] = mapped_column(Integer, nullable=False)
    identity_kind: Mapped[str] = mapped_column(
        String(20), nullable=False
    )
    tax_number: Mapped[str] = mapped_column(
        String(50), nullable=False
    )
    evidence_reference: Mapped[str] = mapped_column(
        String(150), nullable=False
    )
    valid_from: Mapped[date] = mapped_column(
        Date, nullable=False
    )
    valid_to: Mapped[date | None] = mapped_column(
        Date, nullable=True
    )
    review_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending"
    )
    created_by: Mapped[int] = mapped_column(
        Integer, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    reviewed_by: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        Index(
            "ix_taxpayer_identity_review_employee_dates",
            "company_id",
            "employee_id",
            "valid_from",
        ),
        ForeignKeyConstraint(
            ["company_id", "employee_id"],
            ["employees.company_id", "employees.id"],
            name="fk_taxpayer_identity_review_employee",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name="fk_taxpayer_identity_review_creator",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["reviewed_by"],
            ["users.id"],
            name="fk_taxpayer_identity_review_reviewer",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "company_id", "id",
            name="uq_taxpayer_identity_review_company_id",
        ),
        CheckConstraint(
            "identity_kind = 'rnokpp'",
            name="ck_taxpayer_identity_kind",
        ),
        CheckConstraint(
            "tax_number ~ '^[0-9]{10}$'",
            name="ck_taxpayer_identity_number",
        ),
        CheckConstraint(
            "length(trim(evidence_reference)) > 0",
            name="ck_taxpayer_identity_reference",
        ),
        CheckConstraint(
            "valid_to IS NULL OR valid_to >= valid_from",
            name="ck_taxpayer_identity_validity",
        ),
        CheckConstraint(
            "review_status IN ('pending','verified','rejected')",
            name="ck_taxpayer_identity_status",
        ),
        CheckConstraint(
            "(review_status = 'pending' AND "
            "reviewed_by IS NULL AND reviewed_at IS NULL) OR "
            "(review_status IN ('verified','rejected') AND "
            "reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL "
            "AND reviewed_by <> created_by)",
            name="ck_taxpayer_identity_independent_review",
        ),
    )
