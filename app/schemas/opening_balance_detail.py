from datetime import date
from decimal import Decimal
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from app.schemas.opening_balance import OpeningBalanceCreate


class OpeningStockLine(BaseModel):
    model_config = ConfigDict(extra='forbid')
    product_id: int = Field(gt=0)
    warehouse_id: int = Field(gt=0)
    quantity: Decimal = Field(gt=0, max_digits=18, decimal_places=4)
    unit_cost: Decimal = Field(ge=0, max_digits=18, decimal_places=4)


class OpeningDebtLine(BaseModel):
    model_config = ConfigDict(extra='forbid')
    reference: str = Field(min_length=1, max_length=255, pattern=r'.*\S.*')
    counterparty_id: int = Field(gt=0)
    contract_id: int | None = Field(default=None, gt=0)
    item_type: Literal['receivable', 'payable']
    document_date: date
    due_date: date
    amount: Decimal = Field(gt=0, max_digits=18, decimal_places=2)

    @model_validator(mode='after')
    def dates(self):
        if self.due_date < self.document_date:
            raise ValueError('Due date precedes original document date')
        return self


class OpeningFixedAssetLine(BaseModel):
    model_config = ConfigDict(extra='forbid')

    fixed_asset_id: int = Field(gt=0)
    acquisition_date: date
    in_service_date: date
    original_cost: Decimal = Field(
        gt=0,
        max_digits=18,
        decimal_places=2,
    )
    accumulated_depreciation: Decimal = Field(
        ge=0,
        max_digits=18,
        decimal_places=2,
    )

    @model_validator(mode='after')
    def validate_fixed_asset_opening(self):
        if self.in_service_date < self.acquisition_date:
            raise ValueError(
                'In-service date precedes acquisition date'
            )
        if self.accumulated_depreciation > self.original_cost:
            raise ValueError(
                'Accumulated depreciation exceeds original cost'
            )
        return self


class OpeningDetailsCreate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    stock: list[OpeningStockLine] = Field(default_factory=list, max_length=1000)
    debts: list[OpeningDebtLine] = Field(default_factory=list, max_length=1000)
    fixed_assets: list[OpeningFixedAssetLine] = Field(
        default_factory=list,
        max_length=1000,
    )

    @model_validator(mode='after')
    def unique_sources(self):
        if not self.stock and not self.debts and not self.fixed_assets:
            raise ValueError('Provide stock, debt, or fixed asset detail')
        if len({(l.product_id, l.warehouse_id) for l in self.stock}) != len(self.stock):
            raise ValueError('Duplicate product/warehouse opening line')
        keys=[(l.item_type,l.counterparty_id,l.contract_id,l.reference) for l in self.debts]
        if len(set(keys)) != len(keys):
            raise ValueError('Duplicate opening debt reference')
        asset_ids=[l.fixed_asset_id for l in self.fixed_assets]
        if len(set(asset_ids)) != len(asset_ids):
            raise ValueError('Duplicate fixed asset opening line')
        return self


class OpeningPackageCreate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    opening: OpeningBalanceCreate
    details: OpeningDetailsCreate
