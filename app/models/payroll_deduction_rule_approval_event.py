"""Company-scoped audit events for payroll deduction rule approvals.

Events are historical records, not payroll withholding authorization.
Database-level immutability must be enforced by the migration.
"""

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Integer,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class PayrollDeductionRuleApprovalEvent(Base):
    __tablename__ = "payroll_deduction_rule_approval_events"

    __table_args__ = (
        ForeignKeyConstraint(
            ["company_id", "approval_id"],
            [
                "payroll_deduction_rule_approvals.company_id",
                "payroll_deduction_rule_approvals.id",
            ],
            name="fk_pdrae_company_approval",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "event_type IN ('created', 'approved', 'revoked')",
            name="ck_pdrae_event_type",
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

    approval_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    event_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )

    actor_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )

    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
