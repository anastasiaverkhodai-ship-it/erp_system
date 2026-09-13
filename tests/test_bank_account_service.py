from pathlib import Path

import pytest

from app.services.bank_account_service import (
    BankAccountError,
    normalize_bank_account_currency_code,
    normalize_bank_account_name,
    normalize_bank_account_number,
)


def test_bank_account_normalizers():
    assert (
        normalize_bank_account_name(
            "  Main account "
        )
        == "Main account"
    )

    assert (
        normalize_bank_account_number(
            " ua123abc "
        )
        == "UA123ABC"
    )

    assert (
        normalize_bank_account_currency_code(
            " uah "
        )
        == "UAH"
    )


@pytest.mark.parametrize(
    "value",
    [
        "",
        " ",
        "\t",
    ],
)
def test_bank_account_name_rejects_blank(
    value: str,
):
    with pytest.raises(BankAccountError):
        normalize_bank_account_name(value)


@pytest.mark.parametrize(
    "value",
    [
        "",
        " ",
        "\n",
    ],
)
def test_bank_account_number_rejects_blank(
    value: str,
):
    with pytest.raises(BankAccountError):
        normalize_bank_account_number(value)


def test_bank_account_service_does_not_own_transaction():
    source = Path(
        "app/services/bank_account_service.py"
    ).read_text()

    assert ".commit(" not in source
    assert ".rollback(" not in source
