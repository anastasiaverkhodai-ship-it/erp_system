from pathlib import Path

import pytest
from pydantic import ValidationError

from app.schemas.bank_account import (
    BankAccountCreate,
)
from app.services.bank_account_service import (
    BankAccountError,
    normalize_bank_account_currency_code,
    normalize_bank_account_name,
    normalize_bank_account_number,
)


def test_bank_account_permissions_are_company_scoped():
    source = Path(
        "app/api/v1/bank_accounts.py"
    ).read_text()

    assert (
        'prefix="/companies/{company_id}/bank-accounts"'
        in source
    )
    assert '"bank_accounts.read"' in source
    assert '"bank_accounts.manage"' in source


def test_bank_account_service_does_not_commit_or_rollback():
    source = Path(
        "app/services/bank_account_service.py"
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
        normalize_bank_account_currency_code(raw)
        == expected
    )


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "U",
        "UA",
        "UAHH",
        "  ",
    ],
)
def test_invalid_currency_rejected_by_service(
    raw: str,
):
    with pytest.raises(BankAccountError):
        normalize_bank_account_currency_code(
            raw
        )


def test_schema_rejects_invalid_currency_length():
    with pytest.raises(ValidationError):
        BankAccountCreate(
            name="Main",
            account_number="UA123",
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
def test_blank_name_rejected(
    raw: str,
):
    with pytest.raises(BankAccountError):
        normalize_bank_account_name(raw)


@pytest.mark.parametrize(
    "raw",
    [
        "",
        " ",
        "\t",
        "\n",
    ],
)
def test_blank_account_number_rejected(
    raw: str,
):
    with pytest.raises(BankAccountError):
        normalize_bank_account_number(raw)


def test_bank_account_number_is_canonicalized():
    assert (
        normalize_bank_account_number(
            "  ua12ab34  "
        )
        == "UA12AB34"
    )
