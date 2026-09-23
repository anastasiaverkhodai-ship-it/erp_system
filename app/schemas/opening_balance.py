from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.journal_entry import JournalEntryStatus


class OpeningBalanceLineCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: int = Field(gt=0)

    debit: Decimal = Field(
        default=Decimal("0"),
        ge=Decimal("0"),
        max_digits=18,
        decimal_places=2,
    )
    credit: Decimal = Field(
        default=Decimal("0"),
        ge=Decimal("0"),
        max_digits=18,
        decimal_places=2,
    )
    description: str | None = Field(
        default=None,
        max_length=500,
    )

    @model_validator(mode="after")
    def validate_one_side(self):
        debit_positive = self.debit > 0
        credit_positive = self.credit > 0

        if debit_positive == credit_positive:
            raise ValueError(
                "Exactly one of debit or credit must be greater than zero"
            )

        return self


class OpeningBalanceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_key: str = Field(min_length=1, max_length=255, pattern=r".*\S.*")
    opening_date: date
    description: str | None = Field(
        default=None,
        max_length=500,
    )
    lines: list[OpeningBalanceLineCreate] = Field(
        min_length=2,
        max_length=1000,
    )


class OpeningBalanceReverseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reversal_date: date


class OpeningBalanceLineResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    line_no: int
    account_id: int
    debit: Decimal
    credit: Decimal
    description: str | None


class OpeningBalanceResponse(BaseModel):
    id: int
    company_id: int
    opening_date: date
    description: str | None
    journal_entry_id: int
    journal_status: JournalEntryStatus
    created_by: int
    created_at: datetime
    posted_at: datetime | None
    reversed_at: datetime | None
    reversal_journal_entry_id: int | None
    lines: list[OpeningBalanceLineResponse]
