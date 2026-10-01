import enum
from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class EmployeeStatus(str, enum.Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    TERMINATED = "terminated"


class Employee(Base):
    __tablename__ = "employees"

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "employee_number",
            name="uq_employees_company_employee_number",
        ),
        UniqueConstraint(
            "company_id",
            "id",
            name="uq_employees_company_id_id",
        ),
        CheckConstraint(
            "length(trim(employee_number)) > 0",
            name="ck_employees_employee_number_nonempty",
        ),
        CheckConstraint(
            "length(trim(first_name)) > 0",
            name="ck_employees_first_name_nonempty",
        ),
        CheckConstraint(
            "length(trim(last_name)) > 0",
            name="ck_employees_last_name_nonempty",
        ),
        CheckConstraint(
            "termination_date IS NULL OR termination_date >= hire_date",
            name="ck_employees_termination_not_before_hire",
        ),
        CheckConstraint(
            "status <> 'terminated' OR termination_date IS NOT NULL",
            name="ck_employees_terminated_requires_date",
        ),
        CheckConstraint(
            "status = 'terminated' OR termination_date IS NULL",
            name="ck_employees_nonterminated_without_termination_date",
        ),
        Index(
            "ix_employees_company_status",
            "company_id",
            "status",
        ),
        Index(
            "ix_employees_company_user",
            "company_id",
            "user_id",
        ),
    )

    id: Mapped[int] = mapped_column(
        primary_key=True,
    )

    company_id: Mapped[int] = mapped_column(
        ForeignKey(
            "companies.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )

    employee_number: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    first_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    last_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    middle_name: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    tax_number: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )

    payment_iban: Mapped[str | None] = mapped_column(String(29), nullable=True)

    birth_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )

    hire_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )

    termination_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )

    status: Mapped[EmployeeStatus] = mapped_column(
        Enum(
            EmployeeStatus,
            name="employee_status",
            values_callable=lambda enum_cls: [
                item.value for item in enum_cls
            ],
        ),
        nullable=False,
        default=EmployeeStatus.ACTIVE,
        server_default=EmployeeStatus.ACTIVE.value,
    )

    user_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "users.id",
            ondelete="RESTRICT",
        ),
        nullable=True,
    )

    created_by: Mapped[int] = mapped_column(
        ForeignKey(
            "users.id",
            ondelete="RESTRICT",
        ),
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
