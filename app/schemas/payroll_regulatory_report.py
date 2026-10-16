from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class PayrollRegulatoryReportRowRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    company_id: int
    payroll_regulatory_report_id: int
    row_no: int
    employee_id: int | None
    employment_contract_id: int | None
    payroll_calculation_id: int | None
    row_code: str
    payload_json: str
    created_at: datetime


class PayrollRegulatoryReportRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    company_id: int
    payroll_period_id: int
    report_kind: str
    revision: int
    status: str
    generated_at: datetime | None
    generated_by: int | None
    created_by: int
    created_at: datetime


class PayrollRegulatorySourceComponent(BaseModel):
    source_line_id: int
    component: str
    base_amount: Decimal
    rate: Decimal
    amount: Decimal
    currency_code: str


class PayrollRegulatoryCalculationSource(BaseModel):
    employee_id: int
    employment_contract_id: int
    payroll_calculation_id: int
    revision: int
    tax_number: str | None
    issues: list[str]
    gross_amount: Decimal | None
    net_amount: Decimal | None
    employee_withholding_amount: Decimal | None
    employer_contribution_amount: Decimal | None
    currency_code: str | None
    components: list[PayrollRegulatorySourceComponent]


class PayrollRegulatoryReadinessRead(BaseModel):
    company_id: int
    payroll_period_id: int
    report_kind: str
    ready: bool
    applicability: str
    blocking_issues: list[str]
    calculation_sources: list[PayrollRegulatoryCalculationSource]
    notice: str
