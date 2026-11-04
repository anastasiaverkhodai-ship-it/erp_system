from datetime import date, datetime, timezone
from typing import Literal
from decimal import Decimal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class CorrectedTimePremium(BaseModel):
    model_config = ConfigDict(extra='forbid')
    kind: Literal['night','overtime','rest_day','regular_extra']
    minutes: int = Field(gt=0, le=1440, strict=True)
    coefficient: Decimal = Field(default=Decimal(1), ge=0, le=10, max_digits=10, decimal_places=6)


class CorrectedAttendanceDay(BaseModel):
    model_config = ConfigDict(extra='forbid')
    work_date: date
    status: Literal['present','absent']
    worked_minutes: int = Field(ge=0, le=1440, strict=True)
    actual_start_at: datetime | None = None
    actual_end_at: datetime | None = None
    break_minutes: int = Field(default=0, ge=0, le=1440, strict=True)
    premiums: list[CorrectedTimePremium] | None = Field(default=None, max_length=4)

    @model_validator(mode='after')
    def valid_minutes(self):
        if (self.status == 'present') != (self.worked_minutes > 0):
            raise ValueError('Present requires worked minutes; absence requires zero minutes')
        if self.premiums is not None and len({p.kind for p in self.premiums}) != len(self.premiums):
            raise ValueError('Each time-premium type may occur once per date')
        if self.actual_start_at is not None or self.actual_end_at is not None:
            if not self.actual_start_at or not self.actual_end_at or self.actual_start_at.utcoffset() is None or self.actual_end_at.utcoffset() is None:
                raise ValueError('Both attendance timestamps must include a timezone')
            seconds=(self.actual_end_at.astimezone(timezone.utc)-self.actual_start_at.astimezone(timezone.utc)).total_seconds()
            if seconds<=0 or seconds%60 or seconds/60-self.break_minutes!=self.worked_minutes:
                raise ValueError('Attendance interval and breaks must reconcile to worked minutes')
        elif self.break_minutes:
            raise ValueError('Breaks require documented attendance timestamps')
        if not self.worked_minutes and (self.actual_start_at or self.premiums):
            raise ValueError('An absent day cannot include work premiums or timestamps')
        return self


class CorrectedSickCalendar(BaseModel):
    model_config = ConfigDict(extra='forbid',str_strip_whitespace=True)
    original_sick_id: int = Field(gt=0)
    insurance_evidence_id: int = Field(gt=0)
    start_date: date
    end_date: date
    medical_document_reference: str = Field(min_length=10,max_length=1000)

    @model_validator(mode='after')
    def ordered(self):
        if self.end_date<self.start_date:raise ValueError('Corrected sick interval is reversed')
        return self


class AttendanceCorrectionCreate(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    calculation_id: int = Field(gt=0)
    correction_date: date
    request_key: str = Field(min_length=1, max_length=90)
    reason: str = Field(min_length=10, max_length=2000)
    document_reference: str = Field(min_length=3, max_length=1000)
    days: list[CorrectedAttendanceDay] = Field(default_factory=list, max_length=366)
    sick_calendar: CorrectedSickCalendar | None = None

    @model_validator(mode='after')
    def unique_dates(self):
        if not self.days and self.sick_calendar is None:
            raise ValueError("Attendance dates or a corrected medical calendar are required")
        if len({day.work_date for day in self.days}) != len(self.days):
            raise ValueError('Each attendance date may occur once')
        return self
