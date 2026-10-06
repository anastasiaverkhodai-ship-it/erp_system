from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
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


class PayrollDeductionResult(Base):
    __tablename__ = "payroll_deduction_results"

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "id",
            name="uq_payroll_deduction_results_company_id_id",
        ),
        UniqueConstraint(
            "company_id",
            "payroll_calculation_id",
            name="uq_payroll_deduction_results_calculation",
        ),
        ForeignKeyConstraint(
            ["company_id", "payroll_calculation_id"],
            [
                "payroll_calculations.company_id",
                "payroll_calculations.id",
            ],
            name="fk_payroll_deduction_result_calculation",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "payroll_statutory_result_id"],
            [
                "payroll_statutory_results.company_id",
                "payroll_statutory_results.id",
            ],
            name="fk_payroll_deduction_result_statutory",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "employment_contract_id"],
            [
                "employment_contracts.company_id",
                "employment_contracts.id",
            ],
            name="fk_payroll_deduction_result_contract",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "statutory_net_amount >= 0",
            name="ck_payroll_deduction_result_statutory_net",
        ),
        CheckConstraint(
            "deduction_amount >= 0",
            name="ck_payroll_deduction_result_deduction",
        ),
        CheckConstraint(
            "final_payable_amount >= 0",
            name="ck_payroll_deduction_result_final_payable",
        ),
        CheckConstraint(
            "final_payable_amount = "
            "statutory_net_amount - deduction_amount",
            name="ck_payroll_deduction_result_reconciliation",
        ),
        CheckConstraint(
            "currency_code = upper(currency_code) "
            "AND char_length(currency_code) = 3",
            name="ck_payroll_deduction_result_currency",
        ),
        Index(
            "ix_payroll_deduction_result_company_contract",
            "company_id",
            "employment_contract_id",
            "id",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(Integer, nullable=False)

    payroll_calculation_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    payroll_statutory_result_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    employment_contract_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    statutory_net_amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
    )
    deduction_amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
    )
    final_payable_amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
    )
    currency_code: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
    )

    calculated_by: Mapped[int] = mapped_column(Integer, nullable=False)
    calculated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )


class PayrollDeductionResultLine(Base):
    __tablename__ = "payroll_deduction_result_lines"

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "id",
            name="uq_payroll_deduction_result_lines_company_id_id",
        ),
        UniqueConstraint(
            "company_id",
            "payroll_deduction_result_id",
            "payroll_deduction_instruction_id",
            name="uq_payroll_deduction_result_lines_instruction",
        ),
        ForeignKeyConstraint(
            ["company_id", "payroll_deduction_result_id"],
            [
                "payroll_deduction_results.company_id",
                "payroll_deduction_results.id",
            ],
            name="fk_payroll_deduction_result_line_result",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "payroll_deduction_instruction_id"],
            [
                "payroll_deduction_instructions.company_id",
                "payroll_deduction_instructions.id",
            ],
            name="fk_payroll_deduction_result_line_instruction",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "line_no > 0",
            name="ck_payroll_deduction_result_line_no",
        ),
        CheckConstraint(
            "amount > 0",
            name="ck_payroll_deduction_result_line_amount",
        ),
        CheckConstraint(
            "method IN ('fixed', 'percentage')",
            name="ck_payroll_deduction_result_line_method",
        ),
        CheckConstraint(
            "calculation_base_amount >= 0",
            name="ck_payroll_deduction_result_line_base",
        ),
        CheckConstraint(
            "currency_code = upper(currency_code) "
            "AND char_length(currency_code) = 3",
            name="ck_payroll_deduction_result_line_currency",
        ),
        Index(
            "ix_payroll_deduction_result_line_result",
            "company_id",
            "payroll_deduction_result_id",
            "line_no",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(Integer, nullable=False)

    payroll_deduction_result_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    payroll_deduction_instruction_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    line_no: Mapped[int] = mapped_column(Integer, nullable=False)

    deduction_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )
    method: Mapped[str] = mapped_column(String(20), nullable=False)

    calculation_base_amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
    )
    fixed_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 2),
        nullable=True,
    )
    percentage: Mapped[Decimal | None] = mapped_column(
        Numeric(7, 4),
        nullable=True,
    )
    amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
    )

    priority: Mapped[int] = mapped_column(Integer, nullable=False)
    currency_code: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
    )
    source_reference: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
