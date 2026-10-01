from datetime import date, datetime

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from app.models.employment_contract import (
    EmploymentContractStatus,
    WorkArrangement,
)


def _required_text(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("value cannot be blank")
    return normalized


class DepartmentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=255)
    parent_id: int | None = Field(default=None, gt=0)

    @field_validator("code", "name")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        return _required_text(value)


class DepartmentUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str | None = Field(
        default=None,
        min_length=1,
        max_length=100,
    )
    name: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
    )
    parent_id: int | None = Field(default=None, gt=0)
    is_active: bool | None = None

    @field_validator("code", "name")
    @classmethod
    def normalize_text(
        cls,
        value: str | None,
    ) -> str | None:
        if value is None:
            return None
        return _required_text(value)


class DepartmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    company_id: int
    code: str
    name: str
    parent_id: int | None
    is_active: bool
    created_by: int
    created_at: datetime
    updated_at: datetime


class PositionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=255)

    @field_validator("code", "name")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        return _required_text(value)


class PositionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str | None = Field(
        default=None,
        min_length=1,
        max_length=100,
    )
    name: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
    )
    is_active: bool | None = None

    @field_validator("code", "name")
    @classmethod
    def normalize_text(
        cls,
        value: str | None,
    ) -> str | None:
        if value is None:
            return None
        return _required_text(value)


class PositionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    company_id: int
    code: str
    name: str
    is_active: bool
    created_by: int
    created_at: datetime
    updated_at: datetime


class EmploymentContractCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    employee_id: int = Field(gt=0)
    contract_number: str = Field(min_length=1, max_length=100)
    contract_type: str = Field(min_length=1, max_length=100)
    department_id: int | None = Field(default=None, gt=0)
    position_id: int | None = Field(default=None, gt=0)
    work_arrangement: WorkArrangement = WorkArrangement.FULL_TIME
    start_date: date
    end_date: date | None = None
    status: EmploymentContractStatus = EmploymentContractStatus.ACTIVE

    @field_validator("contract_number", "contract_type")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        return _required_text(value)

    @model_validator(mode="after")
    def validate_lifecycle(self):
        if (
            self.end_date is not None
            and self.end_date < self.start_date
        ):
            raise ValueError(
                "end_date cannot precede start_date"
            )

        if (
            self.status == EmploymentContractStatus.ACTIVE
            and self.end_date is not None
        ):
            raise ValueError(
                "active status requires end_date to be null"
            )

        if (
            self.status == EmploymentContractStatus.ENDED
            and self.end_date is None
        ):
            raise ValueError(
                "ended status requires end_date"
            )

        return self


class EmploymentContractUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_number: str | None = Field(
        default=None,
        min_length=1,
        max_length=100,
    )
    contract_type: str | None = Field(
        default=None,
        min_length=1,
        max_length=100,
    )
    department_id: int | None = Field(default=None, gt=0)
    position_id: int | None = Field(default=None, gt=0)
    work_arrangement: WorkArrangement | None = None
    start_date: date | None = None
    end_date: date | None = None
    status: EmploymentContractStatus | None = None

    @field_validator("contract_number", "contract_type")
    @classmethod
    def normalize_text(
        cls,
        value: str | None,
    ) -> str | None:
        if value is None:
            return None
        return _required_text(value)


class EmploymentContractResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    company_id: int
    employee_id: int
    contract_number: str
    contract_type: str
    department_id: int | None
    position_id: int | None
    work_arrangement: WorkArrangement
    start_date: date
    end_date: date | None
    status: EmploymentContractStatus
    created_by: int
    created_at: datetime
    updated_at: datetime
