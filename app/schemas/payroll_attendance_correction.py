from datetime import date
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class CorrectedAttendanceDay(BaseModel):
    model_config = ConfigDict(extra='forbid')
    work_date: date
    status: Literal['present','absent']
    worked_minutes: int = Field(ge=0, le=1440, strict=True)

    @model_validator(mode='after')
    def valid_minutes(self):
        if (self.status == 'present') != (self.worked_minutes > 0):
            raise ValueError('Present requires worked minutes; absence requires zero minutes')
        return self


class AttendanceCorrectionCreate(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    calculation_id: int = Field(gt=0)
    correction_date: date
    request_key: str = Field(min_length=1, max_length=90)
    reason: str = Field(min_length=10, max_length=2000)
    document_reference: str = Field(min_length=3, max_length=1000)
    days: list[CorrectedAttendanceDay] = Field(min_length=1, max_length=31)

    @model_validator(mode='after')
    def unique_dates(self):
        if len({day.work_date for day in self.days}) != len(self.days):
            raise ValueError('Each attendance date may occur once')
        return self
