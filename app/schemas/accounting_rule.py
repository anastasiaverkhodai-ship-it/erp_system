from datetime import datetime

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)

from app.models.accounting_rule_line import (
    AccountingAmountSource,
    AccountingRuleSide,
)
from app.models.document import DocumentType


class AccountingRuleLineCreate(BaseModel):
    account_id: int

    side: AccountingRuleSide

    amount_source: AccountingAmountSource

    description: str | None = Field(
        default=None,
        max_length=500,
    )


class AccountingRuleCreate(BaseModel):
    code: str = Field(
        min_length=1,
        max_length=100,
    )

    name: str = Field(
        min_length=1,
        max_length=255,
    )

    document_type: DocumentType

    lines: list[AccountingRuleLineCreate] = Field(
        min_length=2,
    )

    @model_validator(mode="after")
    def validate_sides(self):
        sides = {
            line.side
            for line in self.lines
        }

        if AccountingRuleSide.DEBIT not in sides:
            raise ValueError(
                "Accounting rule must contain "
                "at least one debit line"
            )

        if AccountingRuleSide.CREDIT not in sides:
            raise ValueError(
                "Accounting rule must contain "
                "at least one credit line"
            )

        return self


class AccountingRuleUpdate(BaseModel):
    code: str | None = Field(
        default=None,
        min_length=1,
        max_length=100,
    )

    name: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
    )

    document_type: DocumentType | None = None

    is_active: bool | None = None

    lines: list[AccountingRuleLineCreate] | None = Field(
        default=None,
        min_length=2,
    )

    @model_validator(mode="after")
    def validate_sides(self):
        if self.lines is None:
            return self

        sides = {
            line.side
            for line in self.lines
        }

        if AccountingRuleSide.DEBIT not in sides:
            raise ValueError(
                "Accounting rule must contain "
                "at least one debit line"
            )

        if AccountingRuleSide.CREDIT not in sides:
            raise ValueError(
                "Accounting rule must contain "
                "at least one credit line"
            )

        return self


class AccountingRuleLineResponse(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
    )

    id: int
    line_no: int
    account_id: int
    side: AccountingRuleSide
    amount_source: AccountingAmountSource
    description: str | None


class AccountingRuleResponse(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
    )

    id: int
    company_id: int
    code: str
    name: str
    document_type: DocumentType
    is_active: bool
    created_at: datetime

    lines: list[AccountingRuleLineResponse]