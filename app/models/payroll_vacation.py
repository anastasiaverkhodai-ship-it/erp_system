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


class PayrollVacationCalculation(Base):
    __tablename__ = "payroll_vacation_calculations"

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "id",
            name="uq_payroll_vacation_calculations_company_id_id",
        ),
        UniqueConstraint(
            "company_id",
            "leave_request_id",
            name="uq_payroll_vacation_calculations_leave_request",
        ),
        ForeignKeyConstraint(
            ["company_id", "leave_request_id"],
            ["leave_requests.company_id", "leave_requests.id"],
            name="fk_payroll_vacation_calculation_leave",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "employment_contract_id"],
            [
                "employment_contracts.company_id",
                "employment_contracts.id",
            ],
            name="fk_payroll_vacation_calculation_contract",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "leave_days > 0",
            name="ck_payroll_vacation_calculation_leave_days",
        ),
        CheckConstraint(
            "reference_period_end >= reference_period_start",
            name="ck_payroll_vacation_calculation_reference_period",
        ),
        CheckConstraint(
            "eligible_earnings >= 0",
            name="ck_payroll_vacation_calculation_earnings",
        ),
        CheckConstraint(
            "eligible_days > 0",
            name="ck_payroll_vacation_calculation_eligible_days",
        ),
        CheckConstraint(
            "average_daily_amount >= 0",
            name="ck_payroll_vacation_calculation_average",
        ),
        CheckConstraint(
            "vacation_pay_amount >= 0",
            name="ck_payroll_vacation_calculation_amount",
        ),
        CheckConstraint(
            "currency_code = 'UAH'",
            name="ck_payroll_vacation_calculation_currency",
        ),
        CheckConstraint(
            "length(trim(rule_code)) > 0",
            name="ck_payroll_vacation_calculation_rule_code",
        ),
        CheckConstraint(
            "length(trim(rule_version)) > 0",
            name="ck_payroll_vacation_calculation_rule_version",
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

    leave_days: Mapped[Decimal] = mapped_column(
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

    vacation_pay_amount: Mapped[Decimal] = mapped_column(
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


class PayrollVacationCalculationSource(Base):
    __tablename__ = "payroll_vacation_calculation_sources"

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "id",
            name="uq_payroll_vacation_sources_company_id_id",
        ),
        UniqueConstraint(
            "company_id",
            "vacation_calculation_id",
            "source_type",
            "source_id",
            name="uq_payroll_vacation_sources_source",
        ),
        ForeignKeyConstraint(
            ["company_id", "vacation_calculation_id"],
            [
                "payroll_vacation_calculations.company_id",
                "payroll_vacation_calculations.id",
            ],
            name="fk_payroll_vacation_source_calculation",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "source_type IN "
            "('earnings_history','payroll_calculation','leave_opening')",
            name="ck_payroll_vacation_source_type",
        ),
        CheckConstraint(
            "source_id > 0",
            name="ck_payroll_vacation_source_id",
        ),
        CheckConstraint(
            "earnings_amount >= 0",
            name="ck_payroll_vacation_source_earnings",
        ),
        CheckConstraint(
            "eligible_days >= 0",
            name="ck_payroll_vacation_source_days",
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

    vacation_calculation_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    source_type: Mapped[str] = mapped_column(
        String(40),
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
        String(500),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
