from datetime import date, datetime, time
from enum import Enum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Time,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AttendanceStatus(str, Enum):
    PRESENT = "present"
    ABSENT = "absent"
    LEAVE = "leave"
    SICK = "sick"
    HOLIDAY = "holiday"
    DAY_OFF = "day_off"


class AttendanceSource(str, Enum):
    MANUAL = "manual"
    IMPORT = "import"
    SYSTEM = "system"


class WorkSchedule(Base):
    __tablename__ = "work_schedules"

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "id",
            name="uq_work_schedules_company_id_id",
        ),
        UniqueConstraint(
            "company_id",
            "code",
            name="uq_work_schedules_company_code",
        ),
        CheckConstraint(
            "length(trim(code)) > 0",
            name="ck_work_schedules_code_nonempty",
        ),
        CheckConstraint(
            "length(trim(name)) > 0",
            name="ck_work_schedules_name_nonempty",
        ),
        CheckConstraint(
            "length(trim(timezone)) > 0",
            name="ck_work_schedules_timezone_nonempty",
        ),
        Index(
            "ix_work_schedules_company_active",
            "company_id",
            "is_active",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="RESTRICT"),
        nullable=False,
    )

    code: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    timezone: Mapped[str] = mapped_column(String(100), nullable=False)

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
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


class WorkScheduleDay(Base):
    __tablename__ = "work_schedule_days"

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "id",
            name="uq_work_schedule_days_company_id_id",
        ),
        UniqueConstraint(
            "company_id",
            "work_schedule_id",
            "weekday",
            name="uq_work_schedule_days_company_schedule_weekday",
        ),
        ForeignKeyConstraint(
            ["company_id", "work_schedule_id"],
            ["work_schedules.company_id", "work_schedules.id"],
            name="fk_work_schedule_days_company_schedule",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "weekday >= 1 AND weekday <= 7",
            name="ck_work_schedule_days_weekday",
        ),
        CheckConstraint(
            "break_minutes >= 0",
            name="ck_work_schedule_days_break_nonnegative",
        ),
        CheckConstraint(
            "("
            "is_working_day = true "
            "AND start_time IS NOT NULL "
            "AND end_time IS NOT NULL "
            "AND end_time > start_time"
            ") OR ("
            "is_working_day = false "
            "AND start_time IS NULL "
            "AND end_time IS NULL"
            ")",
            name="ck_work_schedule_days_working_interval",
        ),
        Index(
            "ix_work_schedule_days_company_schedule",
            "company_id",
            "work_schedule_id",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(Integer, nullable=False)
    work_schedule_id: Mapped[int] = mapped_column(Integer, nullable=False)

    weekday: Mapped[int] = mapped_column(Integer, nullable=False)

    is_working_day: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
    )

    start_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    end_time: Mapped[time | None] = mapped_column(Time, nullable=True)

    break_minutes: Mapped[int] = mapped_column(
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

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class EmploymentScheduleAssignment(Base):
    __tablename__ = "employment_schedule_assignments"

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "id",
            name="uq_employment_schedule_assignments_company_id_id",
        ),
        ForeignKeyConstraint(
            ["company_id", "employment_contract_id"],
            ["employment_contracts.company_id", "employment_contracts.id"],
            name="fk_employment_schedule_assignments_company_contract",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "work_schedule_id"],
            ["work_schedules.company_id", "work_schedules.id"],
            name="fk_employment_schedule_assignments_company_schedule",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="ck_employment_schedule_assignments_date_range",
        ),
        Index(
            "ix_employment_schedule_assignments_company_contract",
            "company_id",
            "employment_contract_id",
            "effective_from",
        ),
        Index(
            "ix_employment_schedule_assignments_company_schedule",
            "company_id",
            "work_schedule_id",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(Integer, nullable=False)

    employment_contract_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    work_schedule_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)

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


class AttendanceRecord(Base):
    __tablename__ = "attendance_records"

    __table_args__ = (
        ForeignKeyConstraint(
            ["company_id", "leave_request_id"],
            ["leave_requests.company_id", "leave_requests.id"],
            name="fk_attendance_records_company_leave_request",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "company_id",
            "id",
            name="uq_attendance_records_company_id_id",
        ),
        UniqueConstraint(
            "company_id",
            "employment_contract_id",
            "work_date",
            name="uq_attendance_records_company_contract_date",
        ),
        ForeignKeyConstraint(
            ["company_id", "employment_contract_id"],
            ["employment_contracts.company_id", "employment_contracts.id"],
            name="fk_attendance_records_company_contract",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "status IN "
            "('present','absent','leave','sick','holiday','day_off')",
            name="ck_attendance_records_status",
        ),
        CheckConstraint(
            "source IN ('manual','import','system')",
            name="ck_attendance_records_source",
        ),
        CheckConstraint(
            "break_minutes >= 0",
            name="ck_attendance_records_break_nonnegative",
        ),
        CheckConstraint(
            "worked_minutes >= 0",
            name="ck_attendance_records_worked_nonnegative",
        ),
        CheckConstraint(
            "actual_end_at IS NULL "
            "OR actual_start_at IS NULL "
            "OR actual_end_at >= actual_start_at",
            name="ck_attendance_records_actual_range",
        ),
        CheckConstraint(
            "("
            "status = 'present' "
            "AND actual_start_at IS NOT NULL "
            "AND actual_end_at IS NOT NULL"
            ") OR ("
            "status <> 'present' "
            "AND actual_start_at IS NULL "
            "AND actual_end_at IS NULL "
            "AND worked_minutes = 0"
            ")",
            name="ck_attendance_records_status_time",
        ),
        Index(
            "ix_attendance_records_company_contract_date",
            "company_id",
            "employment_contract_id",
            "work_date",
        ),
        Index(
            "ix_attendance_records_company_work_date",
            "company_id",
            "work_date",
        ),
        Index(
            "ix_attendance_records_company_status",
            "company_id",
            "status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(Integer, nullable=False)

    employment_contract_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    leave_request_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    work_date: Mapped[date] = mapped_column(Date, nullable=False)

    status: Mapped[AttendanceStatus] = mapped_column(
        String(20),
        nullable=False,
    )

    actual_start_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    actual_end_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    break_minutes: Mapped[int] = mapped_column(
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

    source: Mapped[AttendanceSource] = mapped_column(
        String(20),
        nullable=False,
        default=AttendanceSource.MANUAL,
        server_default=AttendanceSource.MANUAL.value,
    )

    note: Mapped[str | None] = mapped_column(
        String(1000),
        nullable=True,
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
