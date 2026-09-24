from datetime import date
from decimal import Decimal as D
import pytest
from test_output_vat_gl_control import rows
from app.services.accounting_account_roles import AccountingAccountRole as R
from app.services.input_vat_gl_reconciliation_service import reconcile_input_vat_rows

DAY = date(2026, 9, 1)
IDS = {R.TAX_SETTLEMENT: 641, R.VAT_INPUT: 644}


def input_rows(**kwargs):
    data = rows(**kwargs)
    event = data[0][0]
    event.payment_settlement_allocation_id = None
    event.invoice_fulfillment_allocation_id = None
    event.order_vat_advance_id = None
    event.tax_credit_evidence_id = 3
    for _, _, _, line, _ in data:
        if line:
            line.account_id = 644 if line.account_id == 641 else 641
    return data


def check(data):
    return reconcile_input_vat_rows(company_id=1, date_from=DAY, date_to=DAY,
                                   rows=data, account_ids=IDS)


@pytest.mark.parametrize("reversed", [False, True])
def test_input_and_reversal(reversed):
    result = check(input_rows(reversed=reversed))
    assert result.matched
    assert result.posted_input_vat == result.expected_input_vat == D(-12 if reversed else 12)


@pytest.mark.parametrize("kwargs,code", [
    ({"journal": False}, "missing_journal"),
    ({"status": "draft"}, "journal_not_posted"),
    ({"entry_date": date(2026, 10, 1)}, "journal_date_mismatch"),
    ({"amount": "NaN"}, "invalid_event_amount"),
])
def test_anomalies(kwargs, code):
    result = check(input_rows(**kwargs))
    assert not result.matched
    assert code in [i.code for i in result.issues]


def test_input_requires_exclusive_legal_evidence():
    data = input_rows()
    data[0][0].payment_settlement_allocation_id = 2
    assert "invalid_event_source" in [i.code for i in check(data).issues]
    data[0][0].payment_settlement_allocation_id = None
    data[0][0].tax_credit_evidence_id = None
    assert not check(data).matched


def test_offsetting_errors_are_not_hidden():
    a, b = input_rows(), input_rows()
    b[0][0].id = 2
    for line in [r[3] for r in a]:
        if line.debit: line.debit += 1
        if line.credit: line.credit += 1
    for line in [r[3] for r in b]:
        if line.debit: line.debit -= 1
        if line.credit: line.credit -= 1
    result = check(a + b)
    assert result.difference == 0 and not result.matched
    assert {i.event_id for i in result.issues} == {1, 2}
