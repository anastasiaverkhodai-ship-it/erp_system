from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from app.models.time_attendance import (
    AttendanceSource,
    AttendanceStatus,
)


def _required_text(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("value cannot be blank")
    return normalized


def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _validate_timezone(value: str) -> str:
    normalized = _required_text(value)
    try:
        ZoneInfo(normalized)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(
            "timezone must be a valid IANA timezone"
        ) from exc
    return normalized


class WorkScheduleDayInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    weekday: int = Field(ge=1, le=7)
    is_working_day: bool
    start_time: time | None = None
    end_time: time | None = None
    break_minutes: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_day(self) -> "WorkScheduleDayInput":
        if self.is_working_day:
            if self.start_time is None or self.end_time is None:
                raise ValueError(
                    "working day requires start_time and end_time"
                )
            if self.end_time <= self.start_time:
                raise ValueError(
                    "end_time must be after start_time"
                )

            gross_minutes = (
                datetime.combine(date.min, self.end_time)
                - datetime.combine(date.min, self.start_time)
            ).seconds // 60

            if self.break_minutes >= gross_minutes:
                raise ValueError(
                    "break_minutes must leave positive scheduled "
                    "net duration"
                )
        else:
            if self.start_time is not None or self.end_time is not None:
                raise ValueError(
                    "non-working day cannot define start_time "
                    "or end_time"
                )
            if self.break_minutes != 0:
                raise ValueError(
                    "non-working day must have zero break_minutes"
                )

        return self


class WorkScheduleDayRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    company_id: int
    work_schedule_id: int
    weekday: int
    is_working_day: bool
    start_time: time | None
    end_time: time | None
    break_minutes: int
    created_at: datetime
    updated_at: datetime


class WorkScheduleCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=50)
    name: str = Field(min_length=1, max_length=200)
    timezone: str = Field(min_length=1, max_length=100)
    days: list[WorkScheduleDayInput] = Field(default_factory=list)

    @field_validator("code", "name")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        return _required_text(value)

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        return _validate_timezone(value)

    @model_validator(mode="after")
    def validate_unique_weekdays(self) -> "WorkScheduleCreate":
        weekdays = [item.weekday for item in self.days]
        if len(weekdays) != len(set(weekdays)):
            raise ValueError(
                "work schedule cannot contain duplicate weekdays"
            )
        return self


class WorkScheduleUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str | None = Field(
        default=None,
        min_length=1,
        max_length=50,
    )
    name: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
    )
    timezone: str | None = Field(
        default=None,
        min_length=1,
        max_length=100,
    )
    is_active: bool | None = None
    days: list[WorkScheduleDayInput] | None = None

    @field_validator("code", "name")
    @classmethod
    def normalize_text(
        cls,
        value: str | None,
    ) -> str | None:
        if value is None:
            return None
        return _required_text(value)

    @field_validator("timezone")
    @classmethod
    def validate_timezone(
        cls,
        value: str | None,
    ) -> str | None:
        if value is None:
            return None
        return _validate_timezone(value)

    @model_validator(mode="after")
    def validate_unique_weekdays(self) -> "WorkScheduleUpdate":
        if self.days is None:
            return self

        weekdays = [item.weekday for item in self.days]
        if len(weekdays) != len(set(weekdays)):
            raise ValueError(
                "work schedule cannot contain duplicate weekdays"
            )
        return self


class WorkScheduleRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    company_id: int
    code: str
    name: str
    timezone: str
    is_active: bool
    created_by: int
    created_at: datetime
    updated_at: datetime


class EmploymentScheduleAssignmentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    work_schedule_id: int = Field(gt=0)
    effective_from: date
    effective_to: date | None = None

    @model_validator(mode="after")
    def validate_dates(
        self,
    ) -> "EmploymentScheduleAssignmentCreate":
        if (
            self.effective_to is not None
            and self.effective_to < self.effective_from
        ):
            raise ValueError(
                "effective_to cannot be before effective_from"
            )
        return self


class EmploymentScheduleAssignmentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    company_id: int
    employment_contract_id: int
    work_schedule_id: int
    effective_from: date
    effective_to: date | None
    created_by: int
    created_at: datetime
    updated_at: datetime


class AttendanceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    employment_contract_id: int = Field(gt=0)
    work_date: date
    status: AttendanceStatus
    actual_start_at: datetime | None = None
    actual_end_at: datetime | None = None
    break_minutes: int = Field(default=0, ge=0)
    source: AttendanceSource = AttendanceSource.MANUAL
    note: str | None = Field(default=None, max_length=1000)

    @field_validator("note")
    @classmethod
    def normalize_note(
        cls,
        value: str | None,
    ) -> str | None:
        return _optional_text(value)

    @model_validator(mode="after")
    def validate_attendance(self) -> "AttendanceCreate":
        if self.status == AttendanceStatus.PRESENT:
            if (
                self.actual_start_at is None
                or self.actual_end_at is None
            ):
                raise ValueError(
                    "present attendance requires actual_start_at "
                    "and actual_end_at"
                )

            if (
                self.actual_start_at.utcoffset() is None
                or self.actual_end_at.utcoffset() is None
            ):
                raise ValueError(
                    "attendance timestamps must be timezone-aware"
                )

            if self.actual_end_at.astimezone(timezone.utc) <= self.actual_start_at.astimezone(timezone.utc):
                raise ValueError(
                    "actual_end_at must be after actual_start_at"
                )
        else:
            if (
                self.actual_start_at is not None
                or self.actual_end_at is not None
            ):
                raise ValueError(
                    "non-present attendance cannot define "
                    "actual worked timestamps"
                )
            if self.break_minutes != 0:
                raise ValueError(
                    "non-present attendance must have zero "
                    "break_minutes"
                )

        return self


class AttendanceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: AttendanceStatus | None = None
    actual_start_at: datetime | None = None
    actual_end_at: datetime | None = None
    break_minutes: int | None = Field(default=None, ge=0)
    source: AttendanceSource | None = None
    note: str | None = Field(default=None, max_length=1000)

    @field_validator("note")
    @classmethod
    def normalize_note(
        cls,
        value: str | None,
    ) -> str | None:
        return _optional_text(value)


class AttendanceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    company_id: int
    employment_contract_id: int
    work_date: date
    status: AttendanceStatus
    actual_start_at: datetime | None
    actual_end_at: datetime | None
    break_minutes: int
    worked_minutes: int
    source: AttendanceSource
    note: str | None
    created_by: int
    created_at: datetime
    updated_at: datetime
