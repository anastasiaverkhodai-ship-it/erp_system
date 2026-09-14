from pydantic import BaseModel, ConfigDict, Field


class ProductCreate(BaseModel):
    name: str = Field(
        min_length=1,
        max_length=255,
    )

    sku: str = Field(
        min_length=1,
        max_length=100,
    )

    base_uom_code: str = Field(
        min_length=1,
        max_length=20,
    )


class ProductUpdate(BaseModel):
    name: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
    )

    sku: str | None = Field(
        default=None,
        min_length=1,
        max_length=100,
    )

    is_active: bool | None = None

    base_uom_code: str | None = Field(
        default=None,
        min_length=1,
        max_length=20,
    )


class ProductResponse(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
    )

    id: int
    company_id: int
    name: str
    sku: str
    base_uom_code: str | None
    is_active: bool