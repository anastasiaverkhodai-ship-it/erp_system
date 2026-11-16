from pydantic import BaseModel, Field
from app.models.company import InventoryValuationMethod

from app.services.chart_of_accounts_template_types import (
    ChartOfAccountsTemplateType,
)



class CompanyCreate(BaseModel):
    salary_payment_day: int = Field(default=7, ge=1, le=7, strict=True)
    name: str = Field(min_length=1, max_length=255)
    edrpou: str | None = Field(
        default=None,
        min_length=8,
        max_length=8,
    )
    vat_number: str | None = Field(
        default=None,
        max_length=20,
    )
    inventory_valuation_method: InventoryValuationMethod = (
        InventoryValuationMethod.FIFO
    )

    chart_of_accounts_template: (
        ChartOfAccountsTemplateType
    ) = ChartOfAccountsTemplateType.GENERAL_291


class CompanyUpdate(BaseModel):
    salary_payment_day: int = Field(default=7, ge=1, le=7, strict=True)
    name: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
    )
    edrpou: str | None = Field(
        default=None,
        min_length=8,
        max_length=8,
    )
    vat_number: str | None = Field(
        default=None,
        max_length=20,
    )

    inventory_valuation_method: (
        InventoryValuationMethod | None
    ) = None

    chart_of_accounts_template: (
        ChartOfAccountsTemplateType | None
    ) = None

    is_active: bool | None = None


class CompanyResponse(BaseModel):
    salary_payment_day: int = 7
    vat_policy_enabled: bool = False
    id: int
    name: str
    edrpou: str | None
    vat_number: str | None
    inventory_valuation_method: InventoryValuationMethod
    chart_of_accounts_template: ChartOfAccountsTemplateType
    is_active: bool

    model_config = {
        "from_attributes": True
    }