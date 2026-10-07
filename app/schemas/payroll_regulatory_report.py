from __future__ import annotations

from datetime import datetime

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
