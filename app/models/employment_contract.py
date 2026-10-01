from datetime import date, datetime
from enum import Enum

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Enum as SQLEnum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class EmploymentContractStatus(str, Enum):
    ACTIVE = "active"
    ENDED = "ended"
    CANCELLED = "cancelled"


class WorkArrangement(str, Enum):
    FULL_TIME = "full_time"
    PART_TIME = "part_time"


class EmploymentContract(Base):
    __tablename__ = "employment_contracts"

    __table_args__ = (
        CheckConstraint("status IN ('active','ended','cancelled')", name='ck_employment_contracts_status'),
        CheckConstraint("work_arrangement IN ('full_time','part_time')", name='ck_employment_contracts_arrangement'),
        UniqueConstraint(
            "company_id",
            "id",
            name="uq_employment_contracts_company_id_id",
        ),
        UniqueConstraint(
            "company_id",
            "contract_number",
            name="uq_employment_contracts_company_number",
        ),
        ForeignKeyConstraint(
            ["company_id", "employee_id"],
            ["employees.company_id", "employees.id"],
            name="fk_employment_contracts_company_employee",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "department_id"],
            ["departments.company_id", "departments.id"],
            name="fk_employment_contracts_company_department",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "position_id"],
            ["positions.company_id", "positions.id"],
            name="fk_employment_contracts_company_position",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "length(trim(contract_number)) > 0",
            name="ck_employment_contracts_number_nonempty",
        ),
        CheckConstraint(
            "length(trim(contract_type)) > 0",
            name="ck_employment_contracts_type_nonempty",
        ),
        CheckConstraint(
            "end_date IS NULL OR end_date >= start_date",
            name="ck_employment_contracts_date_range",
        ),
        CheckConstraint(
            "status <> 'active' OR end_date IS NULL",
            name="ck_employment_contracts_active_without_end_date",
        ),
        CheckConstraint(
            "status <> 'ended' OR end_date IS NOT NULL",
            name="ck_employment_contracts_ended_requires_date",
        ),
        Index(
            "ix_employment_contracts_company_employee",
            "company_id",
            "employee_id",
        ),
        Index(
            "ix_employment_contracts_company_department",
            "company_id",
            "department_id",
        ),
        Index(
            "ix_employment_contracts_company_position",
            "company_id",
            "position_id",
        ),
        Index(
            "ix_employment_contracts_company_status",
            "company_id",
            "status",
        ),
        Index(
            "ix_employment_contracts_company_start_date",
            "company_id",
            "start_date",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="RESTRICT"),
        nullable=False,
    )

    employee_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    contract_number: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    contract_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    department_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    position_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    work_arrangement: Mapped[WorkArrangement] = mapped_column(
        SQLEnum(
            WorkArrangement,
            name="employment_work_arrangement",
            native_enum=False,
            values_callable=lambda enum_cls: [
                item.value for item in enum_cls
            ],
        ),
        nullable=False,
        default=WorkArrangement.FULL_TIME,
        server_default=WorkArrangement.FULL_TIME.value,
    )

    start_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )

    end_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )

    status: Mapped[EmploymentContractStatus] = mapped_column(
        SQLEnum(
            EmploymentContractStatus,
            name="employment_contract_status",
            native_enum=False,
            values_callable=lambda enum_cls: [
                item.value for item in enum_cls
            ],
        ),
        nullable=False,
        default=EmploymentContractStatus.ACTIVE,
        server_default=EmploymentContractStatus.ACTIVE.value,
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
