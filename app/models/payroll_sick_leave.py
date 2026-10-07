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


class PayrollSickLeaveCalculation(Base):
    __tablename__ = "payroll_sick_leave_calculations"

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "id",
            name="uq_payroll_sick_leave_calculations_company_id_id",
        ),
        UniqueConstraint(
            "company_id",
            "leave_request_id",
            name="uq_payroll_sick_leave_calculations_leave_request",
        ),
        ForeignKeyConstraint(
            ["company_id", "leave_request_id"],
            ["leave_requests.company_id", "leave_requests.id"],
            name="fk_payroll_sick_leave_calculation_leave",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "employment_contract_id"],
            [
                "employment_contracts.company_id",
                "employment_contracts.id",
            ],
            name="fk_payroll_sick_leave_calculation_contract",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "sick_days > 0",
            name="ck_payroll_sick_leave_calculation_sick_days",
        ),
        CheckConstraint(
            "reference_period_end >= reference_period_start",
            name="ck_payroll_sick_leave_calculation_reference_period",
        ),
        CheckConstraint(
            "eligible_earnings >= 0",
            name="ck_payroll_sick_leave_calculation_earnings",
        ),
        CheckConstraint(
            "eligible_days > 0",
            name="ck_payroll_sick_leave_calculation_eligible_days",
        ),
        CheckConstraint(
            "average_daily_amount >= 0",
            name="ck_payroll_sick_leave_calculation_average",
        ),
        CheckConstraint(
            "insurance_service_months >= 0",
            name="ck_payroll_sick_leave_calculation_service_months",
        ),
        CheckConstraint(
            "benefit_percent > 0 AND benefit_percent <= 100",
            name="ck_payroll_sick_leave_calculation_benefit_percent",
        ),
        CheckConstraint(
            "employer_days >= 0 AND insurer_days >= 0 "
            "AND employer_days + insurer_days = sick_days",
            name="ck_payroll_sick_leave_calculation_financing_days",
        ),
        CheckConstraint(
            "daily_benefit_amount >= 0",
            name="ck_payroll_sick_leave_calculation_daily_benefit",
        ),
        CheckConstraint(
            "employer_amount >= 0",
            name="ck_payroll_sick_leave_calculation_employer_amount",
        ),
        CheckConstraint(
            "insurer_amount >= 0",
            name="ck_payroll_sick_leave_calculation_insurer_amount",
        ),
        CheckConstraint(
            "sick_pay_amount >= 0 "
            "AND sick_pay_amount = employer_amount + insurer_amount",
            name="ck_payroll_sick_leave_calculation_total_amount",
        ),
        CheckConstraint(
            "currency_code = 'UAH'",
            name="ck_payroll_sick_leave_calculation_currency",
        ),
        CheckConstraint(
            "length(trim(benefit_case_code)) > 0",
            name="ck_payroll_sick_leave_calculation_case",
        ),
        CheckConstraint(
            "length(trim(rule_code)) > 0",
            name="ck_payroll_sick_leave_calculation_rule_code",
        ),
        CheckConstraint(
            "length(trim(rule_version)) > 0",
            name="ck_payroll_sick_leave_calculation_rule_version",
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

    leave_request_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    employment_contract_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    benefit_case_code: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    sick_days: Mapped[Decimal] = mapped_column(
        Numeric(10, 2),
        nullable=False,
    )

    reference_period_start: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )

    reference_period_end: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )

    eligible_earnings: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
    )

    eligible_days: Mapped[Decimal] = mapped_column(
        Numeric(10, 2),
        nullable=False,
    )

    average_daily_amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 6),
        nullable=False,
    )

    insurance_service_months: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    benefit_percent: Mapped[Decimal] = mapped_column(
        Numeric(7, 4),
        nullable=False,
    )

    limited_service_rule_applied: Mapped[bool] = mapped_column(
        nullable=False,
        default=False,
        server_default="false",
    )

    daily_benefit_amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 6),
        nullable=False,
    )

    employer_days: Mapped[Decimal] = mapped_column(
        Numeric(10, 2),
        nullable=False,
    )

    insurer_days: Mapped[Decimal] = mapped_column(
        Numeric(10, 2),
        nullable=False,
    )

    employer_amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
    )

    insurer_amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
    )

    sick_pay_amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
    )

    currency_code: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
        default="UAH",
        server_default="UAH",
    )

    rule_code: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    rule_version: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    calculated_by: Mapped[int] = mapped_column(
        ForeignKey(
            "users.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )

    calculated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class PayrollSickLeaveCalculationSource(Base):
    __tablename__ = "payroll_sick_leave_calculation_sources"

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "id",
            name="uq_payroll_sick_leave_sources_company_id_id",
        ),
        UniqueConstraint(
            "company_id",
            "sick_leave_calculation_id",
            "source_type",
            "source_id",
            name="uq_payroll_sick_leave_sources_source",
        ),
        ForeignKeyConstraint(
            ["company_id", "sick_leave_calculation_id"],
            [
                "payroll_sick_leave_calculations.company_id",
                "payroll_sick_leave_calculations.id",
            ],
            name="fk_payroll_sick_leave_source_calculation",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "source_type IN "
            "('earnings_history','payroll_calculation',"
            "'insurance_service_evidence')",
            name="ck_payroll_sick_leave_source_type",
        ),
        CheckConstraint(
            "source_id > 0",
            name="ck_payroll_sick_leave_source_id",
        ),
        CheckConstraint(
            "earnings_amount >= 0",
            name="ck_payroll_sick_leave_source_earnings",
        ),
        CheckConstraint(
            "eligible_days >= 0",
            name="ck_payroll_sick_leave_source_days",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    company_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    sick_leave_calculation_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    source_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    source_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    source_period_start: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )

    source_period_end: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )

    earnings_amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 2),
        nullable=False,
        default=Decimal("0.00"),
        server_default="0",
    )

    eligible_days: Mapped[Decimal] = mapped_column(
        Numeric(10, 2),
        nullable=False,
        default=Decimal("0.00"),
        server_default="0",
    )

    source_reference: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
