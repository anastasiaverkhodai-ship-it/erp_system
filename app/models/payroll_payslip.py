from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class PayrollPayslip(Base):
    __tablename__ = "payroll_payslips"

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "id",
            name="uq_payroll_payslips_company_id_id",
        ),
        UniqueConstraint(
            "company_id",
            "payroll_calculation_id",
            name="uq_payroll_payslips_company_calculation",
        ),
        ForeignKeyConstraint(
            ["company_id", "payroll_calculation_id"],
            [
                "payroll_calculations.company_id",
                "payroll_calculations.id",
            ],
            name="fk_payroll_payslips_company_calculation",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "payroll_statutory_result_id"],
            [
                "payroll_statutory_results.company_id",
                "payroll_statutory_results.id",
            ],
            name="fk_payroll_payslips_company_statutory_result",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "payroll_period_id"],
            [
                "payroll_periods.company_id",
                "payroll_periods.id",
            ],
            name="fk_payroll_payslips_company_period",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "employment_contract_id"],
            [
                "employment_contracts.company_id",
                "employment_contracts.id",
            ],
            name="fk_payroll_payslips_company_contract",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "employee_id"],
            [
                "employees.company_id",
                "employees.id",
            ],
            name="fk_payroll_payslips_company_employee",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "currency_code ~ '^[A-Z]{3}$'",
            name="ck_payroll_payslips_currency",
        ),
        CheckConstraint(
            "gross_amount >= 0",
            name="ck_payroll_payslips_gross_nonnegative",
        ),
        CheckConstraint(
            "employee_withholding_amount >= 0",
            name="ck_payroll_payslips_employee_withholding_nonnegative",
        ),
        CheckConstraint(
            "employer_contribution_amount >= 0",
            name="ck_payroll_payslips_employer_contribution_nonnegative",
        ),
        CheckConstraint(
            "net_amount >= 0",
            name="ck_payroll_payslips_net_nonnegative",
        ),
        CheckConstraint(
            "net_amount = gross_amount - employee_withholding_amount",
            name="ck_payroll_payslips_net_reconciliation",
        ),
        CheckConstraint(
            "non_statutory_deduction_amount >= 0",
            name="ck_payroll_payslips_deduction_nonnegative",
        ),
        CheckConstraint(
            "final_payable_amount >= 0",
            name="ck_payroll_payslips_final_payable_nonnegative",
        ),
        CheckConstraint(
            "final_payable_amount = "
            "net_amount - non_statutory_deduction_amount",
            name="ck_payroll_payslips_final_payable_reconciliation",
        ),
        ForeignKeyConstraint(
            ["company_id", "payroll_deduction_result_id"],
            [
                "payroll_deduction_results.company_id",
                "payroll_deduction_results.id",
            ],
            name="fk_payroll_payslips_company_deduction_result",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "length(btrim(employee_number)) > 0",
            name="ck_payroll_payslips_employee_number_nonempty",
        ),
        CheckConstraint(
            "length(btrim(employee_first_name)) > 0",
            name="ck_payroll_payslips_employee_first_name_nonempty",
        ),
        CheckConstraint(
            "length(btrim(employee_last_name)) > 0",
            name="ck_payroll_payslips_employee_last_name_nonempty",
        ),
        CheckConstraint(
            "length(btrim(contract_number)) > 0",
            name="ck_payroll_payslips_contract_number_nonempty",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )
    company_id: Mapped[int] = mapped_column(
        ForeignKey(
            "companies.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
        index=True,
    )
    payroll_calculation_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    payroll_statutory_result_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    payroll_period_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    employment_contract_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    employee_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    employee_number: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )
    employee_first_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )
    employee_last_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )
    employee_middle_name: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )
    employee_tax_number: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )
    contract_number: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    currency_code: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
    )
    gross_amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
    )
    employee_withholding_amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
    )
    employer_contribution_amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
    )
    net_amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
    )
    payroll_deduction_result_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    non_statutory_deduction_amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
        default=Decimal("0.00"),
    )
    final_payable_amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
    )

    generated_by: Mapped[int] = mapped_column(
        ForeignKey(
            "users.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class PayrollPayslipLine(Base):
    __tablename__ = "payroll_payslip_lines"

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "id",
            name="uq_payroll_payslip_lines_company_id_id",
        ),
        UniqueConstraint(
            "company_id",
            "payroll_payslip_id",
            "line_no",
            name="uq_payroll_payslip_lines_payslip_line_no",
        ),
        ForeignKeyConstraint(
            ["company_id", "payroll_payslip_id"],
            [
                "payroll_payslips.company_id",
                "payroll_payslips.id",
            ],
            name="fk_payroll_payslip_lines_company_payslip",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["company_id", "source_payroll_calculation_line_id"],
            [
                "payroll_calculation_lines.company_id",
                "payroll_calculation_lines.id",
            ],
            name="fk_payroll_payslip_lines_company_calculation_line",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["source_payroll_statutory_result_line_id"],
            ["payroll_statutory_result_lines.id"],
            name="fk_payroll_payslip_lines_statutory_line",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "company_id",
                "source_payroll_deduction_result_line_id",
            ],
            [
                "payroll_deduction_result_lines.company_id",
                "payroll_deduction_result_lines.id",
            ],
            name="fk_payroll_payslip_lines_company_deduction_line",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            """(
                line_kind = 'earning'
                AND source_payroll_calculation_line_id IS NOT NULL
                AND source_payroll_statutory_result_line_id IS NULL
                AND source_payroll_deduction_result_line_id IS NULL
            ) OR (
                line_kind IN ('employee_withholding','employer_contribution')
                AND source_payroll_calculation_line_id IS NULL
                AND source_payroll_statutory_result_line_id IS NOT NULL
                AND source_payroll_deduction_result_line_id IS NULL
            ) OR (
                line_kind = 'non_statutory_deduction'
                AND source_payroll_calculation_line_id IS NULL
                AND source_payroll_statutory_result_line_id IS NULL
                AND source_payroll_deduction_result_line_id IS NOT NULL
            )""",
            name="ck_payroll_payslip_lines_source_by_kind",
        ),
        CheckConstraint(
            "line_no > 0",
            name="ck_payroll_payslip_lines_line_no_positive",
        ),
        CheckConstraint(
            "line_kind IN "
            "('earning','employee_withholding',"
            "'employer_contribution','non_statutory_deduction')",
            name="ck_payroll_payslip_lines_kind",
        ),
        CheckConstraint(
            "line_kind = 'earning' OR amount >= 0",
            name="ck_payroll_payslip_lines_amount_by_kind",
        ),
        CheckConstraint(
            "currency_code ~ '^[A-Z]{3}$'",
            name="ck_payroll_payslip_lines_currency",
        ),
        CheckConstraint(
            "length(btrim(component_code)) > 0",
            name="ck_payroll_payslip_lines_component_nonempty",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )
    company_id: Mapped[int] = mapped_column(
        ForeignKey(
            "companies.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    payroll_payslip_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    line_no: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    line_kind: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )
    component_code: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
    )
    currency_code: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
    )

    source_payroll_calculation_line_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    source_payroll_statutory_result_line_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    source_payroll_deduction_result_line_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
