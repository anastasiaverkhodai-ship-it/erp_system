"""Legal INPUT VAT recognition-event control; does not claim full 641/644 coverage."""
from datetime import date
from decimal import Decimal

from app.services.output_vat_gl_reconciliation_service import (
    OutputVatGlIssue, _reconcile_vat_gl, reconcile_output_vat_rows,
)
from pydantic import BaseModel


class InputVatGlReconciliation(BaseModel):
    company_id: int
    date_from: date
    date_to: date
    event_count: int
    expected_input_vat: Decimal
    posted_input_vat: Decimal
    difference: Decimal
    matched: bool
    issues: list[OutputVatGlIssue]


def _input_result(result):
    data = result.model_dump()
    data["expected_input_vat"] = data.pop("expected_output_vat")
    data["posted_input_vat"] = data.pop("posted_output_vat")
    return InputVatGlReconciliation(**data)


def reconcile_input_vat_rows(**kwargs):
    return _input_result(reconcile_output_vat_rows(**kwargs, direction="input"))


async def reconcile_input_vat_gl(db, *, company_id, date_from, date_to):
    return _input_result(await _reconcile_vat_gl(
        db, company_id=company_id, date_from=date_from, date_to=date_to, direction="input"))
