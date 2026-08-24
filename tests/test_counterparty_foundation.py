from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.models.counterparty import Counterparty
from app.schemas.counterparty import (
    CounterpartyCreate,
    CounterpartyUpdate,
)
from app.services.counterparty_types import (
    CounterpartyType,
    CounterpartyVatStatus,
)


def test_counterparty_type_values() -> None:
    assert {
        item.value
        for item in CounterpartyType
    } == {
        "customer",
        "supplier",
        "both",
    }


def test_counterparty_vat_status_values() -> None:
    assert {
        item.value
        for item in CounterpartyVatStatus
    } == {
        "unknown",
        "non_vat_payer",
        "vat_payer",
    }


def test_counterparty_create_defaults() -> None:
    data = CounterpartyCreate(
        name="Test Counterparty"
    )

    assert (
        data.counterparty_type
        == CounterpartyType.BOTH
    )

    assert (
        data.vat_status
        == CounterpartyVatStatus.UNKNOWN
    )

    assert data.default_currency_code == "UAH"
    assert data.payment_term_days == 0

    assert (
        data.credit_limit
        == Decimal("0.00")
    )


def test_counterparty_currency_normalized() -> None:
    data = CounterpartyCreate(
        name="Test",
        default_currency_code="eur",
    )

    assert data.default_currency_code == "EUR"


def test_counterparty_edrpou_validation() -> None:
    with pytest.raises(ValidationError):
        CounterpartyCreate(
            name="Test",
            edrpou="1234",
        )


def test_counterparty_negative_values_rejected() -> None:
    with pytest.raises(ValidationError):
        CounterpartyCreate(
            name="Test",
            payment_term_days=-1,
        )

    with pytest.raises(ValidationError):
        CounterpartyCreate(
            name="Test",
            credit_limit="-1.00",
        )


def test_counterparty_empty_identifiers_to_none() -> None:
    data = CounterpartyCreate(
        name="Test",
        edrpou=" ",
        tax_number="",
        vat_number="   ",
    )

    assert data.edrpou is None
    assert data.tax_number is None
    assert data.vat_number is None


def test_counterparty_update_currency_normalized() -> None:
    data = CounterpartyUpdate(
        default_currency_code="usd"
    )

    assert data.default_currency_code == "USD"


def test_counterparty_table_constraints() -> None:
    table = Counterparty.__table__

    names = {
        constraint.name
        for constraint in table.constraints
        if constraint.name
    }

    assert (
        "uq_counterparty_company_edrpou"
        in names
    )

    assert (
        "uq_counterparty_company_tax_number"
        in names
    )

    assert (
        "uq_counterparty_company_vat_number"
        in names
    )

    assert (
        "ck_counterparty_payment_term_days_nonnegative"
        in names
    )

    assert (
        "ck_counterparty_credit_limit_nonnegative"
        in names
    )
