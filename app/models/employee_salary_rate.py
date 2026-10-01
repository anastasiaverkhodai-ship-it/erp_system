import enum
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Enum,
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


class SalaryRateType(str, enum.Enum):
    MONTHLY = "monthly"
    HOURLY = "hourly"


class EmployeeSalaryRate(Base):
    __tablename__ = "employee_salary_rates"
    __table_args__ = (
        CheckConstraint(
            "rate_type IN ('monthly', 'hourly')",
            name="ck_employee_salary_rates_rate_type",
        ),
        UniqueConstraint(
            "company_id",
            "id",
            name="uq_employee_salary_rates_company_id_id",
        ),
        ForeignKeyConstraint(
            ["company_id", "employment_contract_id"],
            [
                "employment_contracts.company_id",
                "employment_contracts.id",
            ],
            name="fk_employee_salary_rates_company_contract",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "amount > 0",
            name="ck_employee_salary_rates_amount_positive",
        ),
        CheckConstraint(
            "char_length(currency_code) = 3 AND currency_code = upper(currency_code)",
            name="ck_employee_salary_rates_currency",
        ),
        CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="ck_employee_salary_rates_date_range",
        ),
        Index(
            "ix_employee_salary_rates_company_contract",
            "company_id",
            "employment_contract_id",
        ),
        Index(
            "ix_employee_salary_rates_company_effective_from",
            "company_id",
            "effective_from",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(Integer, nullable=False)
    employment_contract_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    rate_type: Mapped[SalaryRateType] = mapped_column(
        String(20),
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
    effective_from: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )
    effective_to: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )
    created_by: Mapped[int | None] = mapped_column(
        Integer,
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
