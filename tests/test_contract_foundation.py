from datetime import date

import pytest
from pydantic import ValidationError

from app.models.contract import Contract
from app.models.counterparty import (
    Counterparty,
)
from app.schemas.contract import (
    ContractCreate,
    ContractUpdate,
)
from app.services.contract_types import (
    ContractStatus,
    ContractType,
)


def test_contract_type_values() -> None:
    assert {
        item.value
        for item in ContractType
    } == {
        "sales",
        "purchase",
        "mixed",
    }


def test_contract_status_values() -> None:
    assert {
        item.value
        for item in ContractStatus
    } == {
        "draft",
        "active",
        "closed",
    }


def test_contract_create_defaults() -> None:
    data = ContractCreate(
        counterparty_id=1,
        number="C-001",
        contract_type=ContractType.SALES,
        start_date=date(
            2026,
            1,
            1,
        ),
    )

    assert (
        data.status
        == ContractStatus.DRAFT
    )

    assert data.currency_code == "UAH"
    assert data.payment_term_days == 0
    assert data.credit_limit == 0


def test_contract_currency_normalized() -> None:
    data = ContractCreate(
        counterparty_id=1,
        number="C-001",
        contract_type=ContractType.SALES,
        start_date=date(
            2026,
            1,
            1,
        ),
        currency_code="eur",
    )

    assert data.currency_code == "EUR"


def test_unknown_contract_currency_rejected() -> None:
    with pytest.raises(ValidationError):
        ContractCreate(
            counterparty_id=1,
            number="C-001",
            contract_type=(
                ContractType.SALES
            ),
            start_date=date(
                2026,
                1,
                1,
            ),
            currency_code="ABC",
        )


def test_contract_bad_date_range_rejected() -> None:
    with pytest.raises(ValidationError):
        ContractCreate(
            counterparty_id=1,
            number="C-001",
            contract_type=(
                ContractType.SALES
            ),
            start_date=date(
                2026,
                2,
                1,
            ),
            end_date=date(
                2026,
                1,
                1,
            ),
        )


def test_negative_contract_values_rejected() -> None:
    with pytest.raises(ValidationError):
        ContractCreate(
            counterparty_id=1,
            number="C-001",
            contract_type=(
                ContractType.SALES
            ),
            start_date=date(
                2026,
                1,
                1,
            ),
            payment_term_days=-1,
        )

    with pytest.raises(ValidationError):
        ContractCreate(
            counterparty_id=1,
            number="C-001",
            contract_type=(
                ContractType.SALES
            ),
            start_date=date(
                2026,
                1,
                1,
            ),
            credit_limit="-0.01",
        )


def test_contract_update_currency_normalized() -> None:
    data = ContractUpdate(
        currency_code="usd"
    )

    assert data.currency_code == "USD"


def test_counterparty_supports_company_safe_fk() -> None:
    names = {
        constraint.name
        for constraint
        in Counterparty.__table__.constraints
        if constraint.name
    }

    assert (
        "uq_counterparties_company_id_id"
        in names
    )


def test_contract_table_constraints() -> None:
    table = Contract.__table__

    names = {
        constraint.name
        for constraint
        in table.constraints
        if constraint.name
    }

    expected = {
        "uq_contracts_company_id_id",
        "uq_contract_company_counterparty_number",
        "fk_contracts_company_counterparty",
        "contract_type_enum",
        "contract_status_enum",
        "ck_contract_date_range",
        "ck_contract_payment_term_days_nonnegative",
        "ck_contract_credit_limit_nonnegative",
        "ck_contract_currency_code_length",
    }

    assert expected <= names


def test_contract_company_safe_counterparty_fk() -> None:
    table = Contract.__table__

    fk = next(
        constraint
        for constraint
        in table.constraints
        if (
            constraint.name
            == "fk_contracts_company_counterparty"
        )
    )

    assert tuple(
        element.parent.name
        for element in fk.elements
    ) == (
        "company_id",
        "counterparty_id",
    )

    assert tuple(
        element.target_fullname
        for element in fk.elements
    ) == (
        "counterparties.company_id",
        "counterparties.id",
    )
