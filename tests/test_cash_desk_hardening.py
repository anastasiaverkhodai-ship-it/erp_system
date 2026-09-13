from pathlib import Path

import pytest
from pydantic import ValidationError

from app.schemas.cash_desk import CashDeskCreate
from app.services.cash_desk_service import (
    CashDeskError,
    normalize_cash_desk_code,
    normalize_cash_desk_currency_code,
    normalize_cash_desk_name,
)


def test_cash_desk_permissions_are_company_scoped():
    source = Path(
        "app/api/v1/cash_desks.py"
    ).read_text()

    assert (
        'prefix="/companies/{company_id}/cash-desks"'
        in source
    )
    assert '"cash_desks.read"' in source
    assert '"cash_desks.manage"' in source


def test_cash_desk_service_does_not_commit_or_rollback():
    source = Path(
        "app/services/cash_desk_service.py"
    ).read_text()

    assert ".commit(" not in source
    assert ".rollback(" not in source


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (" uah ", "UAH"),
        ("usd", "USD"),
        ("EUR", "EUR"),
    ],
)
def test_currency_normalization(
    raw: str,
    expected: str,
):
    assert (
        normalize_cash_desk_currency_code(raw)
        == expected
    )


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "U",
        "UA",
        "UAHH",
        " ",
    ],
)
def test_invalid_currency_rejected_by_service(
    raw: str,
):
    with pytest.raises(CashDeskError):
        normalize_cash_desk_currency_code(raw)


def test_schema_rejects_invalid_currency_length():
    with pytest.raises(ValidationError):
        CashDeskCreate(
            name="Main",
            code="MAIN",
            currency_code="UA",
            accounting_account_id=1,
        )


@pytest.mark.parametrize(
    "raw",
    [
        "",
        " ",
        "\t",
        "\n",
    ],
)
def test_blank_name_rejected(raw: str):
    with pytest.raises(CashDeskError):
        normalize_cash_desk_name(raw)


@pytest.mark.parametrize(
    "raw",
    [
        "",
        " ",
        "\t",
        "\n",
    ],
)
def test_blank_code_rejected(raw: str):
    with pytest.raises(CashDeskError):
        normalize_cash_desk_code(raw)


def test_cash_desk_code_is_canonicalized():
    assert (
        normalize_cash_desk_code(
            "  cash-main  "
        )
        == "CASH-MAIN"
    )


def test_cash_desk_does_not_touch_payment_or_gl():
    source = Path(
        "app/services/cash_desk_service.py"
    ).read_text()

    assert "Payment(" not in source
    assert "JournalEntry(" not in source
    assert "post_journal_entry" not in source
