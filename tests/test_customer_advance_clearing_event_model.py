from datetime import date
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
    UniqueConstraint,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from app.models import (
    CustomerAdvanceClearingEvent,
)


def _columns(
    constraint,
):
    return tuple(
        column.name
        for column
        in constraint.columns
    )


def _foreign_key_contracts():
    result = []

    for constraint in (
        CustomerAdvanceClearingEvent
        .__table__
        .constraints
    ):
        if not isinstance(
            constraint,
            ForeignKeyConstraint,
        ):
            continue

        local = tuple(
            element.parent.name
            for element
            in constraint.elements
        )

        remote = tuple(
            element.target_fullname
            for element
            in constraint.elements
        )

        result.append(
            (
                local,
                remote,
            )
        )

    return tuple(
        result
    )


def test_customer_event_is_exported_with_expected_table():
    assert (
        CustomerAdvanceClearingEvent.__tablename__
        == "customer_advance_clearing_events"
    )


def test_customer_event_column_contract():
    assert tuple(
        column.name
        for column
        in (
            CustomerAdvanceClearingEvent
            .__table__
            .columns
        )
    ) == (
        "id",
        "company_id",
        "payment_settlement_allocation_id",
        "sales_recognition_event_id",
        "clearing_date",
        "cleared_amount",
        "currency_code",
        "created_by",
        "created_at",
        "reversal_of_id",
    )


def test_customer_event_source_pair_is_required():
    table = (
        CustomerAdvanceClearingEvent
        .__table__
    )

    assert (
        table
        .c
        .payment_settlement_allocation_id
        .nullable
        is False
    )

    assert (
        table
        .c
        .sales_recognition_event_id
        .nullable
        is False
    )


def test_customer_event_company_scoped_settlement_fk():
    contracts = _foreign_key_contracts()

    assert (
        (
            (
                "company_id",
                "payment_settlement_allocation_id",
            ),
            (
                "payment_settlement_allocations.company_id",
                "payment_settlement_allocations.id",
            ),
        )
        in contracts
    )


def test_customer_event_company_scoped_sales_recognition_fk():
    contracts = _foreign_key_contracts()

    assert (
        (
            (
                "company_id",
                "sales_recognition_event_id",
            ),
            (
                "sales_recognition_events.company_id",
                "sales_recognition_events.id",
            ),
        )
        in contracts
    )


def test_customer_event_reversal_fk_preserves_pair_provenance():
    contracts = _foreign_key_contracts()

    assert (
        (
            (
                "company_id",
                "reversal_of_id",
                "payment_settlement_allocation_id",
                "sales_recognition_event_id",
            ),
            (
                "customer_advance_clearing_events.company_id",
                "customer_advance_clearing_events.id",
                (
                    "customer_advance_clearing_events."
                    "payment_settlement_allocation_id"
                ),
                (
                    "customer_advance_clearing_events."
                    "sales_recognition_event_id"
                ),
            ),
        )
        in contracts
    )


def test_customer_event_one_reversal_per_original():
    unique_sets = {
        _columns(
            constraint
        )
        for constraint
        in (
            CustomerAdvanceClearingEvent
            .__table__
            .constraints
        )
        if isinstance(
            constraint,
            UniqueConstraint,
        )
    }

    assert (
        "reversal_of_id",
    ) in unique_sets


def test_customer_event_has_reversal_source_unique_identity():
    unique_sets = {
        _columns(
            constraint
        )
        for constraint
        in (
            CustomerAdvanceClearingEvent
            .__table__
            .constraints
        )
        if isinstance(
            constraint,
            UniqueConstraint,
        )
    }

    assert (
        (
            "company_id",
            "id",
            "payment_settlement_allocation_id",
            "sales_recognition_event_id",
        )
        in unique_sets
    )


def test_customer_event_positive_amount_constraint():
    checks = tuple(
        str(
            constraint.sqltext
        )
        .replace(
            " ",
            "",
        )
        .lower()
        for constraint
        in (
            CustomerAdvanceClearingEvent
            .__table__
            .constraints
        )
        if isinstance(
            constraint,
            CheckConstraint,
        )
    )

    assert any(
        "cleared_amount>0"
        in check
        for check in checks
    )


def test_customer_event_currency_length_constraint():
    checks = tuple(
        str(
            constraint.sqltext
        )
        .replace(
            " ",
            "",
        )
        .lower()
        for constraint
        in (
            CustomerAdvanceClearingEvent
            .__table__
            .constraints
        )
        if isinstance(
            constraint,
            CheckConstraint,
        )
    )

    assert any(
        (
            "char_length(currency_code)=3"
            in check
            or "length(currency_code)=3"
            in check
        )
        for check in checks
    )


def test_customer_event_no_self_reversal_constraint():
    checks = tuple(
        str(
            constraint.sqltext
        )
        .replace(
            " ",
            "",
        )
        .lower()
        for constraint
        in (
            CustomerAdvanceClearingEvent
            .__table__
            .constraints
        )
        if isinstance(
            constraint,
            CheckConstraint,
        )
    )

    assert any(
        (
            "reversal_of_idisnull"
            in check
            and "reversal_of_id<>id"
            in check
        )
        for check in checks
    )


def test_customer_event_constraint_names_do_not_reuse_supplier_prefix():
    names = {
        constraint.name
        for constraint
        in (
            CustomerAdvanceClearingEvent
            .__table__
            .constraints
        )
        if constraint.name
        is not None
    }

    assert names

    assert not any(
        "sac_event"
        in name
        for name in names
    )

    assert any(
        "cac_event"
        in name
        for name in names
    )


def test_customer_event_can_represent_original():
    event = CustomerAdvanceClearingEvent(
        company_id=1,
        payment_settlement_allocation_id=10,
        sales_recognition_event_id=20,
        clearing_date=date(
            2026,
            9,
            2,
        ),
        cleared_amount=Decimal(
            "60.00"
        ),
        currency_code="UAH",
        created_by=1,
        reversal_of_id=None,
    )

    assert (
        event
        .payment_settlement_allocation_id
        == 10
    )

    assert (
        event
        .sales_recognition_event_id
        == 20
    )

    assert (
        event.clearing_date
        == date(
            2026,
            9,
            2,
        )
    )

    assert (
        event.cleared_amount
        == Decimal(
            "60.00"
        )
    )

    assert event.reversal_of_id is None


def test_customer_event_can_represent_reversal():
    event = CustomerAdvanceClearingEvent(
        company_id=1,
        payment_settlement_allocation_id=10,
        sales_recognition_event_id=20,
        clearing_date=date(
            2026,
            9,
            4,
        ),
        cleared_amount=Decimal(
            "60.00"
        ),
        currency_code="UAH",
        created_by=1,
        reversal_of_id=100,
    )

    assert event.reversal_of_id == 100


def test_customer_event_postgresql_ddl_contains_both_sources():
    ddl = str(
        CreateTable(
            CustomerAdvanceClearingEvent
            .__table__
        ).compile(
            dialect=(
                postgresql.dialect()
            )
        )
    )

    assert (
        "payment_settlement_allocations"
        in ddl
    )

    assert (
        "sales_recognition_events"
        in ddl
    )

    assert (
        "customer_advance_clearing_events"
        in ddl
    )
