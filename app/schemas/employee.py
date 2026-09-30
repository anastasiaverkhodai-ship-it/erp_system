from datetime import date, datetime

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from app.models.employee import EmployeeStatus


def _normalize_required_text(value: str) -> str:
    normalized = value.strip()

    if not normalized:
        raise ValueError("value cannot be blank")

    return normalized


def _normalize_optional_text(
    value: str | None,
) -> str | None:
    if value is None:
        return None

    normalized = value.strip()

    return normalized or None


class EmployeeCreate(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )

    employee_number: str = Field(
        min_length=1,
        max_length=100,
    )
    first_name: str = Field(
        min_length=1,
        max_length=100,
    )
    last_name: str = Field(
        min_length=1,
        max_length=100,
    )
    middle_name: str | None = Field(
        default=None,
        max_length=100,
    )
    tax_number: str | None = Field(
        default=None,
        max_length=50,
    )
    birth_date: date | None = None
    hire_date: date
    termination_date: date | None = None
    status: EmployeeStatus = EmployeeStatus.ACTIVE
    user_id: int | None = Field(
        default=None,
        gt=0,
    )

    @field_validator(
        "employee_number",
        "first_name",
        "last_name",
    )
    @classmethod
    def normalize_required_text(
        cls,
        value: str,
    ) -> str:
        return _normalize_required_text(value)

    @field_validator(
        "middle_name",
        "tax_number",
    )
    @classmethod
    def normalize_optional_text(
        cls,
        value: str | None,
    ) -> str | None:
        return _normalize_optional_text(value)

    @model_validator(mode="after")
    def validate_lifecycle(self):
        if (
            self.termination_date is not None
            and self.termination_date < self.hire_date
        ):
            raise ValueError(
                "termination_date cannot precede hire_date"
            )

        if (
            self.status == EmployeeStatus.TERMINATED
            and self.termination_date is None
        ):
            raise ValueError(
                "terminated status requires termination_date"
            )

        if (
            self.status != EmployeeStatus.TERMINATED
            and self.termination_date is not None
        ):
            raise ValueError(
                "termination_date requires terminated status"
            )

        return self


class EmployeeUpdate(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )

    employee_number: str | None = Field(
        default=None,
        min_length=1,
        max_length=100,
    )
    first_name: str | None = Field(
        default=None,
        min_length=1,
        max_length=100,
    )
    last_name: str | None = Field(
        default=None,
        min_length=1,
        max_length=100,
    )
    middle_name: str | None = Field(
        default=None,
        max_length=100,
    )
    tax_number: str | None = Field(
        default=None,
        max_length=50,
    )
    birth_date: date | None = None
    hire_date: date | None = None
    termination_date: date | None = None
    status: EmployeeStatus | None = None
    user_id: int | None = Field(
        default=None,
        gt=0,
    )

    @field_validator(
        "employee_number",
        "first_name",
        "last_name",
    )
    @classmethod
    def normalize_required_text(
        cls,
        value: str | None,
    ) -> str | None:
        if value is None:
            return None

        return _normalize_required_text(value)

    @field_validator(
        "middle_name",
        "tax_number",
    )
    @classmethod
    def normalize_optional_text(
        cls,
        value: str | None,
    ) -> str | None:
        return _normalize_optional_text(value)


class EmployeeResponse(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
    )

    id: int
    company_id: int
    employee_number: str
    first_name: str
    last_name: str
    middle_name: str | None
    tax_number: str | None
    birth_date: date | None
    hire_date: date
    termination_date: date | None
    status: EmployeeStatus
    user_id: int | None
    created_by: int
    created_at: datetime
    updated_at: datetime
