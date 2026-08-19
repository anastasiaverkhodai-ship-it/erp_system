from pydantic import BaseModel, Field
from app.models.company import InventoryValuationMethod


class CompanyCreate(BaseModel):
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


class CompanyUpdate(BaseModel):
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

    is_active: bool | None = None


class CompanyResponse(BaseModel):
    id: int
    name: str
    edrpou: str | None
    vat_number: str | None
    inventory_valuation_method: InventoryValuationMethod
    is_active: bool

    model_config = {
        "from_attributes": True
    }