from datetime import date, datetime
from decimal import Decimal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)

from app.models.journal_entry import JournalEntryStatus


class JournalEntryLineCreate(BaseModel):
    account_id: int

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
    def validate_debit_credit(
        self,
    ):
        debit_positive = self.debit > 0
        credit_positive = self.credit > 0

        if debit_positive == credit_positive:
            raise ValueError(
                "Exactly one of debit or credit "
                "must be greater than zero"
            )

        return self


class JournalEntryCreate(BaseModel):
    entry_date: date

    description: str | None = Field(
        default=None,
        max_length=500,
    )

    lines: list[JournalEntryLineCreate] = Field(
        min_length=2,
    )


class JournalEntryUpdate(BaseModel):
    entry_date: date | None = None

    description: str | None = Field(
        default=None,
        max_length=500,
    )

    lines: list[JournalEntryLineCreate] | None = Field(
        default=None,
        min_length=2,
    )

class JournalEntryReverseRequest(BaseModel):
    reversal_date: date

class JournalEntryLineResponse(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
    )

    id: int
    line_no: int
    account_id: int
    debit: Decimal
    credit: Decimal
    description: str | None


class JournalEntryResponse(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
    )

    id: int
    company_id: int
    document_id: int | None
    payment_id: int | None
    payment_settlement_allocation_id: int | None
    accounting_rule_id: int | None

    entry_date: date
    description: str | None

    status: JournalEntryStatus

    created_by: int
    created_at: datetime

    posted_at: datetime | None

    reversed_at: datetime | None
    reversed_by: int | None

    reversal_of_id: int | None

    lines: list[JournalEntryLineResponse]