from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from app.services.payroll_severance_average_earnings import (
    SeveranceAverageError,
)
from app.services.payroll_severance_monthly_sources import (
    SeveranceMonthlyEvidence,
    derive_average_from_monthly_evidence,
    preceding_two_months,
)


def monthly(month, source_id, amount="12000"):
    return SeveranceMonthlyEvidence(
        month=month,
        eligible_earnings=Decimal(amount),
        worked_days=Decimal("20"),
        scheduled_days=Decimal("22"),
        source_type="verified_payroll_severance_basis",
        source_id=source_id,
        source_reference=f"Payroll {source_id}",
        company_id=1,
        employment_contract_id=10,
        verified_finalized=True,
    )


def records():
    return [
        monthly(date(2026, 7, 1), 101),
        monthly(date(2026, 8, 1), 102),
    ]


def calculate(evidence=None, **changes):
    args = dict(
        company_id=1,
        employment_contract_id=10,
        termination_date=date(2026, 9, 30),
        evidence=records() if evidence is None else evidence,
    )
    args.update(changes)
    return derive_average_from_monthly_evidence(**args)


def test_positive_reconciled_two_months():
    result = calculate()
    assert result.average_monthly == Decimal("13200.00")
    assert result.reference_start == date(2026, 7, 1)
    assert result.reference_end == date(2026, 8, 31)
    assert "101" in result.source_reference
    assert "102" in result.source_reference


def test_order_of_sources_does_not_change_amount():
    assert calculate(records()[::-1]) == calculate(records())


def test_january_year_boundary():
    assert preceding_two_months(date(2026, 1, 15)) == (
        date(2025, 11, 1),
        date(2025, 12, 1),
        date(2025, 12, 31),
    )


@pytest.mark.parametrize("evidence", [
    [],
    [monthly(date(2026, 8, 1), 101)],
    [
        monthly(date(2026, 8, 1), 101),
        monthly(date(2026, 8, 1), 102),
    ],
    [
        monthly(date(2026, 6, 1), 101),
        monthly(date(2026, 8, 1), 102),
    ],
    [
        monthly(date(2026, 7, 1), 101),
        monthly(date(2026, 8, 1), 101),
    ],
])
def test_incomplete_or_duplicate_sources(evidence):
    with pytest.raises(SeveranceAverageError):
        calculate(evidence)


@pytest.mark.parametrize("change", [
    {"company_id": 2},
    {"employment_contract_id": 11},
    {"verified_finalized": False},
    {"source_type": "earnings_history"},
    {"source_type": "payroll_calculation"},
    {"source_reference": ""},
    {"eligible_earnings": Decimal("NaN")},
    {"eligible_earnings": Decimal("-1")},
    {"worked_days": Decimal("0")},
    {"worked_days": Decimal("50")},
    {"scheduled_days": Decimal("0")},
    {"source_id": True},
])
def test_untrusted_source_rejected(change):
    rows = records()
    rows[0] = replace(rows[0], **change)
    with pytest.raises(SeveranceAverageError):
        calculate(rows)


def test_wrong_termination_month_rejected():
    with pytest.raises(SeveranceAverageError):
        calculate(termination_date=date(2026, 10, 1))


@pytest.mark.parametrize("bad_id", [0, -1, True])
def test_invalid_company_scope_rejected(bad_id):
    with pytest.raises(SeveranceAverageError):
        calculate(company_id=bad_id)
