import enum
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class PayrollPeriodStatus(str, enum.Enum):
    DRAFT = "draft"
    FINALIZED = "finalized"


class PayrollInputSource(str, enum.Enum):
    SYSTEM = "system"
    MANUAL = "manual"


class PayrollPeriod(Base):
    __tablename__ = "payroll_periods"

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "id",
            name="uq_payroll_periods_company_id_id",
        ),
        UniqueConstraint(
            "company_id",
            "year",
            "month",
            name="uq_payroll_periods_company_year_month",
        ),
        CheckConstraint(
            "year > 0",
            name="ck_payroll_periods_year_positive",
        ),
        CheckConstraint(
            "month >= 1 AND month <= 12",
            name="ck_payroll_periods_month",
        ),
        CheckConstraint(
            "end_date >= start_date",
            name="ck_payroll_periods_date_range",
        ),
        CheckConstraint(
            "status IN ('draft','finalized')",
            name="ck_payroll_periods_status",
        ),
        CheckConstraint(
            """
            (
                status = 'draft'
                AND finalized_by IS NULL
                AND finalized_at IS NULL
            )
            OR
            (
                status = 'finalized'
                AND finalized_by IS NOT NULL
                AND finalized_at IS NOT NULL
            )
            """,
            name="ck_payroll_periods_lifecycle_metadata",
        ),
        Index(
            "ix_payroll_periods_company_status",
            "company_id",
            "status",
        ),
        Index(
            "ix_payroll_periods_company_dates",
            "company_id",
            "start_date",
            "end_date",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="RESTRICT"),
        nullable=False,
    )

    year: Mapped[int] = mapped_column(Integer, nullable=False)
    month: Mapped[int] = mapped_column(Integer, nullable=False)

    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)

    status: Mapped[PayrollPeriodStatus] = mapped_column(
        SAEnum(
            PayrollPeriodStatus,
            name="payroll_period_status",
            native_enum=False,
            values_callable=lambda enum_cls: [
                item.value for item in enum_cls
            ],
        ),
        nullable=False,
        default=PayrollPeriodStatus.DRAFT,
        server_default=PayrollPeriodStatus.DRAFT.value,
    )

    created_by: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )

    finalized_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )

    finalized_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
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


class PayrollInput(Base):
    __tablename__ = "payroll_inputs"

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "id",
            name="uq_payroll_inputs_company_id_id",
        ),
        UniqueConstraint(
            "company_id",
            "payroll_period_id",
            "employment_contract_id",
            name="uq_payroll_inputs_company_period_contract",
        ),
        ForeignKeyConstraint(
            ["company_id", "payroll_period_id"],
            ["payroll_periods.company_id", "payroll_periods.id"],
            name="fk_payroll_inputs_company_period",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "employment_contract_id"],
            [
                "employment_contracts.company_id",
                "employment_contracts.id",
            ],
            name="fk_payroll_inputs_company_contract",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "source IN ('system','manual')",
            name="ck_payroll_inputs_source",
        ),
        CheckConstraint(
            "scheduled_minutes >= 0",
            name="ck_payroll_inputs_scheduled_minutes_nonnegative",
        ),
        CheckConstraint(
            "worked_minutes >= 0",
            name="ck_payroll_inputs_worked_minutes_nonnegative",
        ),
        CheckConstraint(
            "leave_days >= 0",
            name="ck_payroll_inputs_leave_days_nonnegative",
        ),
        CheckConstraint(
            "sick_days >= 0",
            name="ck_payroll_inputs_sick_days_nonnegative",
        ),
        CheckConstraint(
            "manual_adjustment_amount = 0 "
            "OR length(trim(coalesce(manual_adjustment_reason, ''))) > 0",
            name="ck_payroll_inputs_adjustment_reason",
        ),
        Index(
            "ix_payroll_inputs_company_period",
            "company_id",
            "payroll_period_id",
        ),
        Index(
            "ix_payroll_inputs_company_contract",
            "company_id",
            "employment_contract_id",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(Integer, nullable=False)

    payroll_period_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    employment_contract_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    # Full-month contractual norm, including days outside a mid-month hire/end.
    # Null on legacy snapshots: never guess their historical denominator.
    monthly_norm_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)

    scheduled_minutes: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )

    worked_minutes: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )

    leave_days: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )

    sick_days: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )

    manual_adjustment_amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
        default=Decimal("0.00"),
        server_default="0",
    )

    manual_adjustment_reason: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    source: Mapped[PayrollInputSource] = mapped_column(
        SAEnum(
            PayrollInputSource,
            name="payroll_input_source",
            native_enum=False,
            values_callable=lambda enum_cls: [
                item.value for item in enum_cls
            ],
        ),
        nullable=False,
        default=PayrollInputSource.SYSTEM,
        server_default=PayrollInputSource.SYSTEM.value,
    )

    created_by: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
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


class PayrollInputSalarySlice(Base):
    __tablename__ = "payroll_input_salary_slices"

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "id",
            name="uq_payroll_input_salary_slices_company_id_id",
        ),
        ForeignKeyConstraint(
            ["company_id", "payroll_input_id"],
            ["payroll_inputs.company_id", "payroll_inputs.id"],
            name="fk_payroll_salary_slices_company_input",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "salary_rate_id"],
            [
                "employee_salary_rates.company_id",
                "employee_salary_rates.id",
            ],
            name="fk_payroll_salary_slices_company_rate",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "effective_to >= effective_from",
            name="ck_payroll_salary_slices_date_range",
        ),
        CheckConstraint(
            "salary_rate_type IN ('monthly','hourly')",
            name="ck_payroll_salary_slices_rate_type",
        ),
        CheckConstraint(
            "salary_rate_amount > 0",
            name="ck_payroll_salary_slices_amount_positive",
        ),
        CheckConstraint(
            "scheduled_minutes >= 0",
            name="ck_payroll_salary_slices_scheduled_minutes_nonnegative",
        ),
        CheckConstraint(
            "worked_minutes >= 0",
            name="ck_payroll_salary_slices_worked_minutes_nonnegative",
        ),
        CheckConstraint(
            "char_length(currency_code) = 3 "
            "AND currency_code = upper(currency_code)",
            name="ck_payroll_salary_slices_currency",
        ),
        Index(
            "ix_payroll_salary_slices_company_input",
            "company_id",
            "payroll_input_id",
            "effective_from",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(Integer, nullable=False)

    payroll_input_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    salary_rate_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    salary_rate_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )

    salary_rate_amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
    )

    currency_code: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
    )

    effective_from: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )

    effective_to: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )

    scheduled_minutes: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )

    worked_minutes: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

class PayrollCalculationStatus(str, enum.Enum):
    CALCULATED = "calculated"


class PayrollCalculation(Base):
    __tablename__ = "payroll_calculations"

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "id",
            name="uq_payroll_calculations_company_id_id",
        ),
        UniqueConstraint(
            "company_id",
            "payroll_period_id",
            "employment_contract_id",
            name="uq_payroll_calculations_company_period_contract",
        ),
        ForeignKeyConstraint(
            ["company_id", "payroll_period_id"],
            ["payroll_periods.company_id", "payroll_periods.id"],
            name="fk_payroll_calculations_company_period",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "employment_contract_id"],
            ["employment_contracts.company_id", "employment_contracts.id"],
            name="fk_payroll_calculations_company_contract",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "payroll_input_id"],
            ["payroll_inputs.company_id", "payroll_inputs.id"],
            name="fk_payroll_calculations_company_input",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "status IN ('calculated')",
            name="ck_payroll_calculations_status",
        ),
        CheckConstraint(
            "gross_amount >= 0",
            name="ck_payroll_calculations_gross_nonnegative",
        ),
        CheckConstraint(
            "currency_code = upper(currency_code) AND char_length(currency_code) = 3",
            name="ck_payroll_calculations_currency",
        ),
        Index(
            "ix_payroll_calculations_company_period",
            "company_id",
            "payroll_period_id",
        ),
        Index(
            "ix_payroll_calculations_company_contract",
            "company_id",
            "employment_contract_id",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="RESTRICT"),
        nullable=False,
    )

    payroll_period_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    payroll_input_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    employment_contract_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    status: Mapped[PayrollCalculationStatus] = mapped_column(
        SAEnum(
            PayrollCalculationStatus,
            name="payroll_calculation_status",
            native_enum=False,
            values_callable=lambda enum_cls: [
                item.value for item in enum_cls
            ],
        ),
        nullable=False,
        default=PayrollCalculationStatus.CALCULATED,
        server_default=PayrollCalculationStatus.CALCULATED.value,
    )

    currency_code: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
    )

    gross_amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
    )

    calculated_by: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )

    calculated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class PayrollCalculationLine(Base):
    __tablename__ = "payroll_calculation_lines"

    __table_args__ = (
        ForeignKeyConstraint(['company_id','source_supplement_id'],
            ['payroll_supplements.company_id','payroll_supplements.id'],
            name='fk_payroll_calculation_line_supplement',ondelete='RESTRICT'),
        ForeignKeyConstraint(
            ["company_id", "source_vacation_calculation_id"],
            [
                "payroll_vacation_calculations.company_id",
                "payroll_vacation_calculations.id",
            ],
            name="fk_payroll_calculation_line_vacation",
            ondelete="RESTRICT",
        ),

        ForeignKeyConstraint(
            ["company_id", "source_sick_leave_calculation_id"],
            [
                "payroll_sick_leave_calculations.company_id",
                "payroll_sick_leave_calculations.id",
            ],
            name="fk_payroll_calculation_lines_sick_leave",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "company_id",
            "id",
            name="uq_payroll_calculation_lines_company_id_id",
        ),
        UniqueConstraint(
            "company_id",
            "payroll_calculation_id",
            "line_no",
            name="uq_payroll_calculation_lines_company_calculation_line_no",
        ),
        ForeignKeyConstraint(
            ["company_id", "source_salary_rate_id"],
            ["employee_salary_rates.company_id", "employee_salary_rates.id"],
            name="fk_payroll_calculation_lines_company_salary_rate",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "payroll_calculation_id"],
            ["payroll_calculations.company_id", "payroll_calculations.id"],
            name="fk_payroll_calculation_lines_company_calculation",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "line_no > 0",
            name="ck_payroll_calculation_lines_line_no_positive",
        ),
        CheckConstraint(
            "quantity >= 0",
            name="ck_payroll_calculation_lines_quantity_nonnegative",
        ),
        CheckConstraint(
            "(line_type IN ('salary','supplement','vacation_pay','sick_pay') "
            "AND rate IS NOT NULL AND rate >= 0) OR "
            "(line_type = 'manual_adjustment' AND rate IS NOT NULL)",
            name="ck_payroll_calculation_lines_rate",
        ),
        CheckConstraint(
            "(line_type IN ('salary','supplement','vacation_pay','sick_pay') "
            "AND amount >= 0) OR line_type = 'manual_adjustment'",
            name="ck_payroll_calculation_lines_amount",
        ),
        CheckConstraint(
            "(line_type = 'supplement') = (source_supplement_id IS NOT NULL)",
            name="ck_payroll_calculation_line_supplement_source",
        ),
        CheckConstraint(
            "(line_type = 'vacation_pay') = "
            "(source_vacation_calculation_id IS NOT NULL)",
            name="ck_payroll_calculation_line_vacation_source",
        ),

        CheckConstraint(
            "(line_type = 'sick_pay') = "
            "(source_sick_leave_calculation_id IS NOT NULL)",
            name="ck_payroll_calculation_line_sick_source",
        ),
        CheckConstraint(
            "line_type IN ('salary','manual_adjustment','supplement','vacation_pay','sick_pay')",
            name="ck_payroll_calculation_lines_type",
        ),
        CheckConstraint(
            "currency_code = upper(currency_code) AND char_length(currency_code) = 3",
            name="ck_payroll_calculation_lines_currency",
        ),
        Index(
            "ix_payroll_calculation_lines_company_calculation",
            "company_id",
            "payroll_calculation_id",
            "line_no",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="RESTRICT"),
        nullable=False,
    )

    payroll_calculation_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    source_supplement_id: Mapped[int | None] = mapped_column(Integer,nullable=True)

    source_sick_leave_calculation_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    source_vacation_calculation_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    line_no: Mapped[int] = mapped_column(Integer, nullable=False)

    line_type: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )

    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    quantity: Mapped[Decimal] = mapped_column(
        Numeric(18, 4),
        nullable=False,
    )

    rate: Mapped[Decimal] = mapped_column(
        Numeric(18, 4),
        nullable=False,
    )

    amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
    )

    currency_code: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
    )

    salary_rate_type: Mapped[str | None] = mapped_column(
        String(16),
        nullable=True,
    )

    source_salary_rate_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    source_effective_from: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )

    source_effective_to: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
