from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
    Index,
    UniqueConstraint,
)

from app.models.payment import Payment
from app.models.payment_settlement_allocation import (
    PaymentSettlementAllocation,
)
from app.services.payment_types import (
    PaymentDirection,
    PaymentSettlementAllocationStatus,
    PaymentStatus,
)


def constraint_names(table, cls):
    return {
        constraint.name
        for constraint in table.constraints
        if isinstance(
            constraint,
            cls,
        )
    }


def index_names(table):
    return {
        index.name
        for index in table.indexes
    }


def test_payment_type_values():
    assert (
        PaymentDirection.INCOMING.value
        == "incoming"
    )

    assert (
        PaymentDirection.OUTGOING.value
        == "outgoing"
    )

    assert (
        PaymentStatus.DRAFT.value
        == "draft"
    )

    assert (
        PaymentStatus.CONFIRMED.value
        == "confirmed"
    )

    assert (
        PaymentStatus.CANCELLED.value
        == "cancelled"
    )

    assert (
        PaymentSettlementAllocationStatus.ACTIVE.value
        == "active"
    )

    assert (
        PaymentSettlementAllocationStatus.REVERSED.value
        == "reversed"
    )


def test_payment_table_contract():
    table = Payment.__table__

    assert table.name == "payments"

    assert set(
        table.columns.keys()
    ) == {
        "id",
        "company_id",
        "counterparty_id",
        "contract_id",
        "number",
        "direction",
        "status",
        "payment_date",
        "currency_code",
        "amount",
        "external_reference",
        "description",
        "created_by",
        "created_at",
        "updated_at",
        "confirmed_at",
        "cancelled_by",
        "cancelled_at",
    }


def test_payment_has_no_warehouse_or_gl_source_fields():
    columns = set(
        Payment.__table__.columns.keys()
    )

    forbidden = {
        "document_id",
        "warehouse_id",
        "accounting_rule_id",
        "journal_entry_id",
        "stock_ledger_id",
        "open_amount",
        "settled_amount",
    }

    assert not (
        columns & forbidden
    )


def test_payment_unique_constraints():
    names = constraint_names(
        Payment.__table__,
        UniqueConstraint,
    )

    assert (
        "uq_payments_company_id_id"
        in names
    )

    assert (
        "uq_payments_company_direction_number"
        in names
    )


def test_payment_foreign_key_constraints():
    names = constraint_names(
        Payment.__table__,
        ForeignKeyConstraint,
    )

    assert (
        "fk_payments_company_counterparty"
        in names
    )

    assert (
        "fk_payments_company_counterparty_contract"
        in names
    )


def test_payment_check_constraints():
    names = constraint_names(
        Payment.__table__,
        CheckConstraint,
    )

    assert {
        "ck_payments_direction",
        "ck_payments_status",
        "ck_payments_amount_positive",
        "ck_payments_currency_code_length",
        "ck_payments_lifecycle_state",
    } <= names


def test_settlement_allocation_table_contract():
    table = (
        PaymentSettlementAllocation.__table__
    )

    assert (
        table.name
        == "payment_settlement_allocations"
    )

    assert set(
        table.columns.keys()
    ) == {
        "id",
        "company_id",
        "payment_id",
        "open_item_id",
        "amount",
        "status",
        "created_by",
        "created_at",
        "reversed_by",
        "reversed_at",
    }


def test_settlement_allocation_has_no_mutable_balances():
    columns = set(
        PaymentSettlementAllocation
        .__table__
        .columns
        .keys()
    )

    assert "open_amount" not in columns
    assert "settled_amount" not in columns


def test_settlement_allocation_constraints():
    table = (
        PaymentSettlementAllocation.__table__
    )

    unique_names = constraint_names(
        table,
        UniqueConstraint,
    )

    fk_names = constraint_names(
        table,
        ForeignKeyConstraint,
    )

    check_names = constraint_names(
        table,
        CheckConstraint,
    )

    assert (
        "uq_payment_settlement_allocations_company_id_id"
        in unique_names
    )

    assert {
        "fk_payment_settlement_allocations_payment",
        "fk_payment_settlement_allocations_open_item",
    } <= fk_names

    assert {
        "ck_payment_settlement_allocations_amount_positive",
        "ck_payment_settlement_allocations_status",
        "ck_payment_settlement_allocations_reversal_state",
    } <= check_names


def test_settlement_allocation_partial_indexes():
    table = (
        PaymentSettlementAllocation.__table__
    )

    names = index_names(
        table
    )

    assert {
        "ix_payment_settlement_payment_active",
        "ix_payment_settlement_open_item_active",
        "uq_payment_settlement_active_pair",
    } <= names

    active_pair = next(
        index
        for index in table.indexes
        if (
            index.name
            == "uq_payment_settlement_active_pair"
        )
    )

    assert isinstance(
        active_pair,
        Index,
    )

    assert active_pair.unique is True


def test_settlement_amount_is_currency_precision():
    column = (
        PaymentSettlementAllocation
        .__table__
        .c
        .amount
    )

    assert column.type.precision == 18
    assert column.type.scale == 2


def test_payment_amount_is_currency_precision():
    column = (
        Payment.__table__.c.amount
    )

    assert column.type.precision == 18
    assert column.type.scale == 2
