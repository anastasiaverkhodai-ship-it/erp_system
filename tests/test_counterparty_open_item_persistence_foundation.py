from decimal import Decimal

from sqlalchemy import inspect

from app.models.counterparty_open_item import (
    CounterpartyOpenItem,
)
from app.services.counterparty_open_item_types import (
    CounterpartyOpenItemStatus,
    CounterpartyOpenItemType,
)


def test_open_item_types_are_stable():
    assert (
        CounterpartyOpenItemType.RECEIVABLE.value
        == "receivable"
    )

    assert (
        CounterpartyOpenItemType.PAYABLE.value
        == "payable"
    )


def test_open_item_statuses_are_stable():
    assert {
        item.value
        for item in CounterpartyOpenItemStatus
    } == {
        "open",
        "partially_settled",
        "settled",
        "cancelled",
    }


def test_open_item_table_name():
    assert (
        CounterpartyOpenItem.__tablename__
        == "counterparty_open_items"
    )


def test_open_item_has_expected_columns():
    mapper = inspect(
        CounterpartyOpenItem
    )

    names = {
        column.key
        for column in mapper.columns
    }

    assert names == {
        "id",
        "company_id",
        "trade_document_id",
        "counterparty_id",
        "contract_id",
        "item_type",
        "status",
        "document_date",
        "due_date",
        "currency_code",
        "original_amount",
        "created_at",
    }


def test_open_item_does_not_store_mutable_open_balance():
    mapper = inspect(
        CounterpartyOpenItem
    )

    names = {
        column.key
        for column in mapper.columns
    }

    forbidden = {
        "open_amount",
        "balance_due",
        "paid_amount",
        "settled_amount",
    }

    assert not (
        names & forbidden
    )


def test_open_item_original_amount_is_money_precision():
    column = (
        CounterpartyOpenItem
        .__table__
        .c
        .original_amount
    )

    assert column.type.precision == 18
    assert column.type.scale == 2


def test_open_item_contract_is_optional():
    assert (
        CounterpartyOpenItem
        .__table__
        .c
        .contract_id
        .nullable
        is True
    )


def test_open_item_core_fields_are_required():
    table = CounterpartyOpenItem.__table__

    for name in (
        "company_id",
        "trade_document_id",
        "counterparty_id",
        "item_type",
        "status",
        "document_date",
        "due_date",
        "currency_code",
        "original_amount",
    ):
        assert (
            table.c[name].nullable
            is False
        )


def test_open_item_unique_source_invoice_constraint_exists():
    constraints = {
        constraint.name
        for constraint
        in CounterpartyOpenItem
        .__table__
        .constraints
    }

    assert (
        "uq_counterparty_open_items_"
        "company_trade_document"
        in constraints
    )


def test_open_item_company_scoped_fk_constraints_exist():
    constraints = {
        constraint.name
        for constraint
        in CounterpartyOpenItem
        .__table__
        .constraints
        if constraint.name
    }

    assert (
        "fk_counterparty_open_items_"
        "company_trade_document"
        in constraints
    )

    assert (
        "fk_counterparty_open_items_"
        "company_counterparty"
        in constraints
    )

    assert (
        "fk_counterparty_open_items_"
        "company_counterparty_contract"
        in constraints
    )


def test_example_amount_is_positive_money():
    amount = Decimal(
        "251.00"
    )

    assert amount > Decimal("0")
