from datetime import date
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class LeaveAdvanceAuthorization(BaseModel):
    model_config=ConfigDict(extra='forbid')
    reason: Literal['six_months','subsequent_year','disability','minor',
                    'secondary_main_leave','statutory_or_contractual_exception']
    document_date: date
    source_reference: str=Field(min_length=1,max_length=1000)
    exception_clause: str | None=Field(default=None,min_length=1,max_length=1000)
    main_leave_start: date | None=None
    main_leave_end: date | None=None

    @field_validator('source_reference','exception_clause')
    @classmethod
    def nonempty(cls,value):
        if value is not None and not value.strip():
            raise ValueError('Advance authorization needs documentary evidence')
        return value.strip() if value is not None else None

    @model_validator(mode='after')
    def required_evidence(self):
        if self.reason=='statutory_or_contractual_exception' and self.exception_clause is None:
            raise ValueError('Specify the statutory or contractual early-leave clause')
        if self.reason=='secondary_main_leave':
            if self.main_leave_start is None or self.main_leave_end is None or self.main_leave_start>self.main_leave_end:
                raise ValueError('Document the simultaneous leave at the primary employer')
        elif self.main_leave_start is not None or self.main_leave_end is not None:
            raise ValueError('Primary leave dates apply only to secondary employment')
        return self
