from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class PayrollDeductionInstruction(Base):
    __tablename__ = "payroll_deduction_instructions"

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "id",
            name="uq_payroll_deduction_instructions_company_id_id",
        ),
        UniqueConstraint(
            "company_id",
            "employment_contract_id",
            "request_key",
            name="uq_payroll_deduction_instructions_request",
        ),
        ForeignKeyConstraint(
            ["company_id", "employment_contract_id"],
            [
                "employment_contracts.company_id",
                "employment_contracts.id",
            ],
            name="fk_payroll_deduction_instruction_contract",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "method IN ('fixed', 'percentage')",
            name="ck_payroll_deduction_instruction_method",
        ),
        CheckConstraint(
            "priority >= 0",
            name="ck_payroll_deduction_instruction_priority",
        ),
        CheckConstraint(
            """
            (
                method = 'fixed'
                AND fixed_amount IS NOT NULL
                AND fixed_amount > 0
                AND percentage IS NULL
            )
            OR
            (
                method = 'percentage'
                AND percentage IS NOT NULL
                AND percentage > 0
                AND percentage <= 100
                AND fixed_amount IS NULL
            )
            """,
            name="ck_payroll_deduction_instruction_value",
        ),
        CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="ck_payroll_deduction_instruction_dates",
        ),
        CheckConstraint(
            "currency_code = upper(currency_code) "
            "AND char_length(currency_code) = 3",
            name="ck_payroll_deduction_instruction_currency",
        ),
        CheckConstraint(
            "char_length(trim(deduction_type)) > 0",
            name="ck_payroll_deduction_instruction_type",
        ),
        CheckConstraint(
            "char_length(trim(request_key)) > 0",
            name="ck_payroll_deduction_instruction_request_key",
        ),
        Index(
            "ix_payroll_deduction_instruction_contract_dates",
            "company_id",
            "employment_contract_id",
            "effective_from",
            "effective_to",
        ),
        Index(
            "ix_payroll_deduction_instruction_company_priority",
            "company_id",
            "priority",
            "id",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(Integer, nullable=False)
    employment_contract_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    deduction_type: Mapped[str] = mapped_column(String(50), nullable=False)
    method: Mapped[str] = mapped_column(String(20), nullable=False)

    fixed_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 2),
        nullable=True,
    )
    percentage: Mapped[Decimal | None] = mapped_column(
        Numeric(7, 4),
        nullable=True,
    )

    currency_code: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
        default="UAH",
    )

    priority: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=100,
    )

    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)

    request_key: Mapped[str] = mapped_column(String(100), nullable=False)
    source_reference: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    created_by: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
