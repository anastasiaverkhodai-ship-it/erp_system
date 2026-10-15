from datetime import date
from decimal import Decimal
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class AssessmentCreate(BaseModel):
    """Incremental amount newly due; previous unpaid assessments carry separately."""
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    instruction_id: int = Field(gt=0)
    beneficiary_id: int = Field(gt=0)
    rule_approval_id: int = Field(gt=0)
    reference: str = Field(min_length=1, max_length=500)
    request_key: str = Field(min_length=1, max_length=100)
    amount: Decimal = Field(gt=0, max_digits=18, decimal_places=2, allow_inf_nan=False)
    priority: int = Field(ge=0)
    priority_reference: str = Field(min_length=1, max_length=500)
    individual_limit_percentage: Decimal = Field(gt=0, le=70, decimal_places=4, allow_inf_nan=False)
    effective_from: date


class WithholdingPaymentCreate(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    kind: Literal['advance', 'salary', 'opening']
    opening_debt_id: int | None = Field(default=None, gt=0)
    payroll_period_id: int | None = Field(default=None, gt=0)
    employment_contract_id: int | None = Field(default=None, gt=0)
    payroll_calculation_id: int | None = Field(default=None, gt=0)
    bank_account_id: int = Field(gt=0)
    payment_date: date
    advance_percentage: Decimal | None = Field(default=None, gt=0, le=100, decimal_places=4, allow_inf_nan=False)
    amount: Decimal | None = Field(default=None, gt=0, max_digits=18, decimal_places=2, allow_inf_nan=False)
    protected_amount: Decimal = Field(ge=0, max_digits=18, decimal_places=2, allow_inf_nan=False)
    income_reference: str = Field(min_length=1, max_length=500)
    request_key: str = Field(min_length=1, max_length=100)


class RecipientRemittanceAllocation(BaseModel):
    withholding_line_id: int = Field(gt=0)
    amount: Decimal = Field(gt=0, max_digits=18, decimal_places=2, allow_inf_nan=False)


class RecipientRemittanceCreate(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    beneficiary_id: int = Field(gt=0)
    journal_entry_id: int = Field(gt=0)
    bank_statement_line_id: int = Field(gt=0)
    request_key: str = Field(min_length=1, max_length=100)
    allocations: list[RecipientRemittanceAllocation] = Field(min_length=1, max_length=1000)


class DeductionEvidenceCreate(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    legal_category: str = Field(min_length=1, max_length=100)
    document_reference: str = Field(min_length=1, max_length=500)


class DeductionRuleApprovalCreate(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    legal_category: str = Field(min_length=1, max_length=100)
    rule_version: str = Field(min_length=1, max_length=100)
    legal_reference: str = Field(min_length=1, max_length=500)
    valid_from: date
    valid_to: date | None = None
