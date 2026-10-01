from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.models.employee_salary_rate import (
    EmployeeSalaryRate,
    SalaryRateType,
)
from app.schemas.employee_salary_rate import SalaryRateCreate
from app.services.employee_salary_rate_service import (
    _ranges_overlap,
)


def test_salary_rate_table_contract():
    table = EmployeeSalaryRate.__table__

    assert table.name == "employee_salary_rates"

    assert set(table.columns.keys()) == {
        "id",
        "company_id",
        "employment_contract_id",
        "rate_type",
        "amount",
        "currency_code",
        "effective_from",
        "effective_to",
        "created_by",
        "created_at",
        "updated_at",
    }


def test_salary_rate_types():
    assert {
        item.value for item in SalaryRateType
    } == {
        "monthly",
        "hourly",
    }


def test_currency_normalization():
    payload = SalaryRateCreate(
        employment_contract_id=1,
        rate_type=SalaryRateType.MONTHLY,
        amount=Decimal("1000.00"),
        currency_code="uah",
        effective_from=date(2026, 1, 1),
    )

    assert payload.currency_code == "UAH"


@pytest.mark.parametrize(
    "currency",
    ["", "UA", "UAHH", "12A"],
)
def test_invalid_currency_rejected(currency):
    with pytest.raises(ValidationError):
        SalaryRateCreate(
            employment_contract_id=1,
            rate_type=SalaryRateType.MONTHLY,
            amount=Decimal("1000.00"),
            currency_code=currency,
            effective_from=date(2026, 1, 1),
        )


@pytest.mark.parametrize(
    "amount",
    [Decimal("0"), Decimal("-1.00")],
)
def test_non_positive_amount_rejected(amount):
    with pytest.raises(ValidationError):
        SalaryRateCreate(
            employment_contract_id=1,
            rate_type=SalaryRateType.MONTHLY,
            amount=amount,
            currency_code="UAH",
            effective_from=date(2026, 1, 1),
        )


def test_open_ended_ranges_overlap():
    assert _ranges_overlap(
        date(2026, 1, 1),
        None,
        date(2026, 2, 1),
        None,
    )


def test_inclusive_boundary_overlaps():
    assert _ranges_overlap(
        date(2026, 1, 1),
        date(2026, 1, 31),
        date(2026, 1, 31),
        date(2026, 2, 28),
    )


def test_adjacent_ranges_do_not_overlap():
    assert not _ranges_overlap(
        date(2026, 1, 1),
        date(2026, 1, 31),
        date(2026, 2, 1),
        None,
    )


def test_service_transaction_ownership():
    text = Path(
        "app/services/employee_salary_rate_service.py"
    ).read_text()

    assert "await db.flush()" in text
    assert "await db.commit()" not in text
    assert "await db.rollback()" not in text


def test_salary_rate_has_no_accounting_posting():
    text = Path(
        "app/services/employee_salary_rate_service.py"
    ).read_text()

    for token in (
        "JournalEntry",
        "JournalEntryLine",
        "post_journal",
        "reverse_journal",
    ):
        assert token not in text
