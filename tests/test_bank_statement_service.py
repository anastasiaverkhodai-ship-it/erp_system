from decimal import Decimal
from pathlib import Path

import pytest

from app.services.bank_statement_service import (
    BankStatementError,
    BankStatementLineAmountError,
    normalize_bank_statement_currency_code,
    normalize_bank_statement_external_id,
    normalize_bank_statement_line_external_id,
)


def test_statement_normalizers():
    assert (
        normalize_bank_statement_external_id(
            " S-001 "
        )
        == "S-001"
    )

    assert (
        normalize_bank_statement_line_external_id(
            " L-001 "
        )
        == "L-001"
    )

    assert (
        normalize_bank_statement_currency_code(
            " uah "
        )
        == "UAH"
    )


@pytest.mark.parametrize(
    "value",
    ["", " ", "\t"],
)
def test_blank_statement_id_rejected(
    value: str,
):
    with pytest.raises(BankStatementError):
        normalize_bank_statement_external_id(
            value
        )


@pytest.mark.parametrize(
    "value",
    ["", " ", "\n"],
)
def test_blank_line_id_rejected(
    value: str,
):
    with pytest.raises(BankStatementError):
        normalize_bank_statement_line_external_id(
            value
        )


def test_service_has_no_update_delete_or_tx_ownership():
    text = Path(
        "app/services/bank_statement_service.py"
    ).read_text()

    assert ".commit(" not in text
    assert ".rollback(" not in text

    assert "update(BankStatement" not in text
    assert "update(BankStatementLine" not in text
    assert "delete(BankStatement" not in text
    assert "delete(BankStatementLine" not in text
