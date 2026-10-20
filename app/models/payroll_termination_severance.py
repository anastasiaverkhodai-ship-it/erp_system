"""Immutable, versioned source of documented severance pay."""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class PayrollTerminationSeverance(Base):
    __tablename__ = "payroll_termination_severances"

    __table_args__ = (
        UniqueConstraint(
            "company_id", "id",
            name="uq_pts_company_id",
        ),
        UniqueConstraint(
            "company_id", "request_key",
            name="uq_pts_request",
        ),
        UniqueConstraint(
            "company_id", "termination_event_id", "revision",
            name="uq_pts_event_revision",
        ),
        UniqueConstraint(
            "company_id", "supersedes_id",
            name="uq_pts_supersedes",
        ),
        ForeignKeyConstraint(
            ["company_id", "payroll_input_id"],
            ["payroll_inputs.company_id", "payroll_inputs.id"],
            name="fk_pts_input",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "employment_contract_id"],
            ["employment_contracts.company_id", "employment_contracts.id"],
            name="fk_pts_contract",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "termination_event_id"],
            ["employment_events.company_id", "employment_events.id"],
            name="fk_pts_event",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "supersedes_id"],
            [
                "payroll_termination_severances.company_id",
                "payroll_termination_severances.id",
            ],
            name="fk_pts_supersedes",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "revision >= 1",
            name="ck_pts_revision",
        ),
        CheckConstraint(
            "(revision = 1 AND supersedes_id IS NULL) OR "
            "(revision > 1 AND supersedes_id IS NOT NULL)",
            name="ck_pts_revision_link",
        ),
        CheckConstraint(
            "policy_category IN ('average_months','minimum_wages')",
            name="ck_pts_category",
        ),
        CheckConstraint(
            "multiplier > 0",
            name="ck_pts_multiplier",
        ),
        CheckConstraint(
            "base_amount > 0 AND "
            "base_amount <> 'NaN'::numeric AND "
            "gross_amount > 0 AND "
            "gross_amount <> 'NaN'::numeric",
            name="ck_pts_amounts",
        ),
        CheckConstraint(
            "currency_code = 'UAH'",
            name="ck_pts_currency",
        ),
        CheckConstraint(
            "length(trim(request_key)) > 0 AND "
            "length(trim(order_number)) > 0 AND "
            "length(trim(labour_code_provision)) > 0 AND "
            "length(trim(source_reference)) > 0 AND "
            "length(trim(basis_reference)) > 0",
            name="ck_pts_evidence",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True
    )

    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="RESTRICT", name="fk_pts_company"),
        nullable=False,
    )

    payroll_input_id: Mapped[int] = mapped_column(
        Integer, nullable=False
    )

    employment_contract_id: Mapped[int] = mapped_column(
        Integer, nullable=False
    )

    termination_event_id: Mapped[int] = mapped_column(
        Integer, nullable=False
    )

    revision: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1,
        server_default="1",
    )

    supersedes_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )

    request_key: Mapped[str] = mapped_column(
        String(200), nullable=False
    )

    termination_date: Mapped[date] = mapped_column(
        Date, nullable=False
    )

    order_number: Mapped[str] = mapped_column(
        String(100), nullable=False
    )

    policy_ground: Mapped[str] = mapped_column(
        String(64), nullable=False
    )

    policy_category: Mapped[str] = mapped_column(
        String(32), nullable=False
    )

    labour_code_provision: Mapped[str] = mapped_column(
        String(100), nullable=False
    )

    source_reference: Mapped[str] = mapped_column(
        String(1000), nullable=False
    )

    multiplier: Mapped[int] = mapped_column(
        Integer, nullable=False
    )

    basis_as_of: Mapped[date] = mapped_column(
        Date, nullable=False
    )

    basis_reference: Mapped[str] = mapped_column(
        String(1000), nullable=False
    )

    base_amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 2), nullable=False
    )

    gross_amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 2), nullable=False
    )

    currency_code: Mapped[str] = mapped_column(
        String(3), nullable=False,
        default="UAH", server_default="UAH",
    )

    created_by: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT", name="fk_pts_created_by"),
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
