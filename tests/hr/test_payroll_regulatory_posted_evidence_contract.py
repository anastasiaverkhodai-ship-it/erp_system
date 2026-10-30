import pytest
from sqlalchemy.dialects import postgresql

from app.services.payroll_regulatory_payment_evidence import (
    active_posted_journal_query,
)
from app.services.payroll_regulatory_report_service import (
    PayrollRegulatoryReportError,
)


def compile_query(**kwargs):
    return str(
        active_posted_journal_query(**kwargs).compile(
            dialect=postgresql.dialect()
        )
    )


def test_disbursement_requires_no_posted_reversal():
    sql = compile_query(
        company_id=1,
        payroll_disbursement_id=7,
    )
    assert "NOT (EXISTS" in sql
    assert "reversal_of_id" in sql
    assert "payroll_disbursement_id" in sql
    assert "journal_entries_1" in sql


def test_tax_remittance_requires_no_posted_reversal():
    sql = compile_query(
        company_id=1,
        journal_entry_id=9,
    )
    assert "NOT (EXISTS" in sql
    assert "reversal_of_id" in sql
    assert "journal_entries.id" in sql


@pytest.mark.parametrize(
    "kwargs",
    [
        {"company_id": 1},
        {
            "company_id": 1,
            "journal_entry_id": 3,
            "payroll_disbursement_id": 4,
        },
        {
            "company_id": 0,
            "journal_entry_id": 3,
        },
        {
            "company_id": 1,
            "journal_entry_id": -1,
        },
    ],
)
def test_invalid_financial_evidence_scope_rejected(kwargs):
    with pytest.raises(PayrollRegulatoryReportError):
        active_posted_journal_query(**kwargs)
