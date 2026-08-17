from pydantic import BaseModel, Field


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
    is_active: bool | None = None


class CompanyResponse(BaseModel):
    id: int
    name: str
    edrpou: str | None
    vat_number: str | None
    is_active: bool

    model_config = {
        "from_attributes": True
    }