from datetime import date
import pytest

from app.services.payroll_paid_income_calculation import (
    PaidIncomeError,
    paid_income_month,
)
from app.services.payroll_paid_income_service import (
    report_month_for_record,
)
from app.services.payroll_regulatory_payment_evidence import (
    active_posted_journal_query,
)
from app.services.payroll_regulatory_report_service import (
    PayrollRegulatoryReportError,
)


def sample(**overrides):
    body = {
        "payment_date": "2026-10-07",
        "accrual_month": "2026-09",
        "income_line_types": ["salary"],
        "salary_deadline": {"date": "2026-10-07"},
    }
    body.update(overrides)
    return body


@pytest.mark.parametrize(
    "day,expected",
    [
        ("2026-10-06", "2026-09"),
        ("2026-10-07", "2026-09"),
        ("2026-10-08", "2026-10"),
        ("2026-11-02", "2026-11"),
    ],
)
def test_verified_salary_deadline(day, expected):
    assert report_month_for_record(
        sample(payment_date=day)
    ) == expected


@pytest.mark.parametrize(
    "types",
    [
        ["salary", "vacation_pay"],
        ["salary", "sick_pay"],
        ["salary", "severance"],
        ["salary", "leave_compensation"],
        ["manual_adjustment"],
    ],
)
def test_unverified_mixed_income_is_blocked(types):
    with pytest.raises(
        PaidIncomeError, match="classification"
    ):
        report_month_for_record(
            sample(income_line_types=types)
        )


def test_severance_does_not_inherit_salary_exception():
    assert report_month_for_record(
        sample(income_line_types=["severance"])
    ) == "2026-10"


def test_missing_salary_deadline_is_rejected():
    with pytest.raises(PaidIncomeError):
        report_month_for_record(
            sample(salary_deadline=None)
        )


@pytest.mark.parametrize("day", [None, 8, 31])
def test_invalid_deadline_does_not_establish_timeliness(day):
    with pytest.raises(PaidIncomeError):
        paid_income_month(
            accrual_month="2026-09",
            payment_date=date(2026, 10, 7),
            salary=True,
            contractual_due_date=(
                date(2026, 10, day)
                if day is not None else None
            ),
        )


def test_journal_query_requires_exactly_one_source():
    with pytest.raises(
        PayrollRegulatoryReportError,
        match="Exactly one",
    ):
        active_posted_journal_query(
            company_id=1,
            journal_entry_id=3,
            payroll_disbursement_id=2,
        )

    with pytest.raises(
        PayrollRegulatoryReportError,
        match="Exactly one",
    ):
        active_posted_journal_query(company_id=1)


def test_valid_single_journal_source_is_accepted():
    query = active_posted_journal_query(
        company_id=1,
        journal_entry_id=3,
    )
    assert query is not None
