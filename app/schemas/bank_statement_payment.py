from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class BankStatementExistingPaymentMatchRequest(
    BaseModel
):
    model_config = ConfigDict(extra="forbid")

    payment_id: int = Field(gt=0)
    matched_amount: Decimal = Field(
        gt=0,
        max_digits=18,
        decimal_places=2,
    )


class BankStatementCreatePaymentRequest(
    BaseModel
):
    model_config = ConfigDict(extra="forbid")

    counterparty_id: int = Field(gt=0)
    contract_id: int | None = Field(
        default=None,
        gt=0,
    )
    number: str = Field(
        min_length=1,
        max_length=100,
    )
    external_reference: str | None = Field(
        default=None,
        max_length=255,
    )
    description: str | None = Field(
        default=None,
        max_length=500,
    )


class BankStatementPaymentMatchResponse(
    BaseModel
):
    payment_id: int
    bank_account_id: int | None

    reconciliation_id: int
    bank_statement_line_id: int

    matched_amount: Decimal
    currency_code: str
