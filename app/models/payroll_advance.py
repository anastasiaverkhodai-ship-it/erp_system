from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class PayrollAdvance(Base):
    __tablename__ = "payroll_advances"

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "id",
            name="uq_payroll_advances_company_id_id",
        ),
        UniqueConstraint(
            "company_id",
            "payroll_period_id",
            "employment_contract_id",
            name="uq_payroll_advances_company_period_contract",
        ),
        ForeignKeyConstraint(
            ["company_id", "payroll_period_id"],
            ["payroll_periods.company_id", "payroll_periods.id"],
            name="fk_payroll_advances_company_period",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "employment_contract_id"],
            ["employment_contracts.company_id", "employment_contracts.id"],
            name="fk_payroll_advances_company_contract",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "bank_account_id"],
            ["bank_accounts.company_id", "bank_accounts.id"],
            name="fk_payroll_advances_company_bank",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "advance_percentage > 0 AND advance_percentage <= 100",
            name="ck_payroll_advances_percentage",
        ),
        CheckConstraint(
            "calculated_amount > 0",
            name="ck_payroll_advances_calculated_amount_positive",
        ),
        CheckConstraint(
            "minimum_due_amount >= 0",
            name="ck_payroll_advances_minimum_due_nonnegative",
        ),
        CheckConstraint(
            "paid_amount > 0",
            name="ck_payroll_advances_paid_amount_positive",
        ),
        CheckConstraint(
            "paid_amount <= calculated_amount",
            name="ck_payroll_advances_paid_lte_calculated",
        ),
        CheckConstraint(
            "paid_amount >= minimum_due_amount",
            name="ck_payroll_advances_paid_gte_minimum",
        ),
        CheckConstraint(
            "currency_code = upper(currency_code) AND char_length(currency_code) = 3",
            name="ck_payroll_advances_currency",
        ),
        Index(
            "ix_payroll_advances_company_payment_date",
            "company_id",
            "payment_date",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("companies.id", ondelete="RESTRICT"),
        nullable=False,
    )

    payroll_period_id: Mapped[int] = mapped_column(Integer, nullable=False)
    employment_contract_id: Mapped[int] = mapped_column(Integer, nullable=False)
    bank_account_id: Mapped[int] = mapped_column(Integer, nullable=False)

    advance_percentage: Mapped[Decimal] = mapped_column(
        Numeric(7, 4),
        nullable=False,
    )
    calculation_base_amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
    )
    calculated_amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
    )
    minimum_due_amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
    )
    paid_amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
    )

    currency_code: Mapped[str] = mapped_column(String(3), nullable=False)
    payment_date: Mapped[date] = mapped_column(Date, nullable=False)

    created_by: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
