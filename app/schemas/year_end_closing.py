from datetime import datetime
from decimal import Decimal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class ClosingMapping(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_account_id: int = Field(gt=0)
    result_account_id: int = Field(gt=0)


class YearEndPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profit_account_id: int = Field(gt=0)
    loss_account_id: int = Field(gt=0)
    mappings: list[ClosingMapping] = Field(default_factory=list, max_length=1000)

    @model_validator(mode="after")
    def unique_sources(self):
        ids = [m.source_account_id for m in self.mappings]
        if len(ids) != len(set(ids)):
            raise ValueError("Each source account must have exactly one mapping")
        return self


class YearEndCloseRequest(YearEndPreviewRequest):
    request_key: str = Field(min_length=1, max_length=255, pattern=r".*\S.*")
    preview_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


class YearEndPreviewLine(BaseModel):
    account_id: int
    debit: Decimal
    credit: Decimal
    description: str


class YearEndPreview(BaseModel):
    company_id: int
    year: int
    profit: Decimal
    lines: list[YearEndPreviewLine]
    preview_fingerprint: str


class YearEndClosingResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    company_id: int
    year: int
    status: str
    journal_entry_id: int | None
    request_key: str
    preview_fingerprint: str
    created_by: int
    created_at: datetime
    reversed_by: int | None
    reversed_at: datetime | None
    reversal_journal_entry_id: int | None = None
