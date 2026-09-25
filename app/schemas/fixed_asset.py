from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.fixed_asset import (
    FixedAssetDepreciationMethod,
    FixedAssetStatus,
)


class FixedAssetGroupCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    code: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=255)
    is_active: bool = True


class FixedAssetGroupUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    code: str | None = Field(default=None, min_length=1, max_length=100)
    name: str | None = Field(default=None, min_length=1, max_length=255)
    is_active: bool | None = None


class FixedAssetGroupResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    company_id: int
    code: str
    name: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


class FixedAssetLocationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    code: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=255)
    is_active: bool = True


class FixedAssetLocationUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    code: str | None = Field(default=None, min_length=1, max_length=100)
    name: str | None = Field(default=None, min_length=1, max_length=255)
    is_active: bool | None = None


class FixedAssetLocationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    company_id: int
    code: str
    name: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


class FixedAssetResponsiblePersonCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    display_name: str = Field(min_length=1, max_length=255)
    is_active: bool = True


class FixedAssetResponsiblePersonUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    display_name: str | None = Field(default=None, min_length=1, max_length=255)
    is_active: bool | None = None


class FixedAssetResponsiblePersonResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    company_id: int
    display_name: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


class FixedAssetCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    asset_number: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=255)
    acquisition_date: date
    in_service_date: date | None = None
    original_cost: Decimal = Field(ge=0)
    salvage_value: Decimal = Field(default=Decimal("0.00"), ge=0)
    useful_life_months: int = Field(gt=0)
    depreciation_method: FixedAssetDepreciationMethod
    status: FixedAssetStatus = FixedAssetStatus.DRAFT
    asset_account_id: int
    accumulated_depreciation_account_id: int
    depreciation_expense_account_id: int
    asset_group_id: int
    location_id: int | None = None
    responsible_person_id: int | None = None

    @model_validator(mode="after")
    def validate_card(self):
        if self.salvage_value > self.original_cost:
            raise ValueError("salvage_value cannot exceed original_cost")
        if (
            self.in_service_date is not None
            and self.in_service_date < self.acquisition_date
        ):
            raise ValueError("in_service_date cannot precede acquisition_date")
        return self


class FixedAssetUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str | None = Field(default=None, min_length=1, max_length=255)
    in_service_date: date | None = None
    salvage_value: Decimal | None = Field(default=None, ge=0)
    useful_life_months: int | None = Field(default=None, gt=0)
    depreciation_method: FixedAssetDepreciationMethod | None = None
    status: FixedAssetStatus | None = None
    asset_group_id: int | None = None
    location_id: int | None = None
    responsible_person_id: int | None = None
    effective_date: date


class FixedAssetResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    company_id: int
    asset_number: str
    name: str
    acquisition_date: date
    in_service_date: date | None
    original_cost: Decimal
    salvage_value: Decimal
    useful_life_months: int
    depreciation_method: FixedAssetDepreciationMethod
    status: FixedAssetStatus
    asset_account_id: int
    accumulated_depreciation_account_id: int
    depreciation_expense_account_id: int
    asset_group_id: int
    location_id: int | None
    responsible_person_id: int | None
    created_by: int
    created_at: datetime
    updated_at: datetime


class FixedAssetCardHistoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    company_id: int
    fixed_asset_id: int
    effective_date: date
    asset_group_id: int
    location_id: int | None
    responsible_person_id: int | None
    name: str
    useful_life_months: int
    salvage_value: Decimal
    depreciation_method: FixedAssetDepreciationMethod
    changed_by: int
    created_at: datetime
