from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
    Numeric,
    UniqueConstraint,
)

from app.models.sales_return_cost_restoration_event import (
    SalesReturnCostRestorationEvent,
)
from app.models.sales_return_cost_restoration_fifo_slice import (
    SalesReturnCostRestorationFifoSlice,
)


def unique_sets(
    model,
):
    result = set()

    for constraint in model.__table__.constraints:
        if not isinstance(
            constraint,
            UniqueConstraint,
        ):
            continue

        result.add(
            tuple(
                column.name
                for column
                in constraint.columns
            )
        )

    return result


def foreign_key(
    model,
    name,
):
    return next(
        constraint
        for constraint
        in model.__table__.constraints
        if (
            isinstance(
                constraint,
                ForeignKeyConstraint,
            )
            and constraint.name
            == name
        )
    )


def check_sql(
    model,
    name,
):
    constraint = next(
        constraint
        for constraint
        in model.__table__.constraints
        if (
            isinstance(
                constraint,
                CheckConstraint,
            )
            and constraint.name
            == name
        )
    )

    return " ".join(
        str(
            constraint.sqltext
        ).split()
    )


def test_parent_columns():
    columns = (
        SalesReturnCostRestorationEvent
        .__table__
        .c
    )

    expected = {
        "id",
        "company_id",
        "trade_return_event_id",
        "inventory_cost_entry_id",
        "restoration_date",
        "valuation_method",
        "restored_quantity",
        "restored_valuation_amount",
        "restored_cost_amount",
        "aggregate_historical_unit_cost",
        "created_by",
        "created_at",
        "reversal_of_id",
    }

    assert set(
        columns.keys()
    ) == expected


def test_parent_numeric_precision():
    columns = (
        SalesReturnCostRestorationEvent
        .__table__
        .c
    )

    quantity = columns[
        "restored_quantity"
    ].type

    valuation = columns[
        "restored_valuation_amount"
    ].type

    cost = columns[
        "restored_cost_amount"
    ].type

    unit_cost = columns[
        "aggregate_historical_unit_cost"
    ].type

    assert isinstance(
        quantity,
        Numeric,
    )

    assert (
        quantity.precision,
        quantity.scale,
    ) == (
        18,
        4,
    )

    assert (
        valuation.precision,
        valuation.scale,
    ) == (
        24,
        8,
    )

    assert (
        cost.precision,
        cost.scale,
    ) == (
        18,
        2,
    )

    assert (
        unit_cost.precision,
        unit_cost.scale,
    ) == (
        24,
        8,
    )


def test_parent_source_unique_identity():
    sets = unique_sets(
        SalesReturnCostRestorationEvent
    )

    assert (
        "company_id",
        "id",
    ) in sets

    assert (
        "company_id",
        "id",
        "trade_return_event_id",
        "inventory_cost_entry_id",
    ) in sets

    assert (
        "reversal_of_id",
    ) in sets


def test_parent_trade_return_company_fk():
    fk = foreign_key(
        SalesReturnCostRestorationEvent,
        "fk_srcre_event_trade_return",
    )

    assert tuple(
        column.name
        for column
        in fk.columns
    ) == (
        "company_id",
        "trade_return_event_id",
    )

    assert tuple(
        str(
            element.column
        )
        for element
        in fk.elements
    ) == (
        "trade_return_events.company_id",
        "trade_return_events.id",
    )

    assert fk.ondelete == "RESTRICT"


def test_parent_reversal_preserves_pair_source():
    fk = foreign_key(
        SalesReturnCostRestorationEvent,
        "fk_srcre_event_reversal_source",
    )

    assert tuple(
        column.name
        for column
        in fk.columns
    ) == (
        "company_id",
        "reversal_of_id",
        "trade_return_event_id",
        "inventory_cost_entry_id",
    )

    assert tuple(
        str(
            element.column
        )
        for element
        in fk.elements
    ) == (
        (
            "sales_return_cost_restoration_events."
            "company_id"
        ),
        (
            "sales_return_cost_restoration_events."
            "id"
        ),
        (
            "sales_return_cost_restoration_events."
            "trade_return_event_id"
        ),
        (
            "sales_return_cost_restoration_events."
            "inventory_cost_entry_id"
        ),
    )


def test_parent_pair_history_is_not_unique():
    table = (
        SalesReturnCostRestorationEvent
        .__table__
    )

    index = next(
        index
        for index
        in table.indexes
        if index.name
        == "ix_srcre_event_pair_history"
    )

    assert index.unique is False

    assert tuple(
        column.name
        for column
        in index.columns
    ) == (
        "company_id",
        "trade_return_event_id",
        "inventory_cost_entry_id",
        "id",
    )


def test_parent_has_no_unique_original_pair():
    table = (
        SalesReturnCostRestorationEvent
        .__table__
    )

    forbidden_pair = {
        "company_id",
        "trade_return_event_id",
        "inventory_cost_entry_id",
    }

    for constraint in table.constraints:
        if not isinstance(
            constraint,
            UniqueConstraint,
        ):
            continue

        assert set(
            column.name
            for column
            in constraint.columns
        ) != forbidden_pair

    for index in table.indexes:
        if not index.unique:
            continue

        assert set(
            column.name
            for column
            in index.columns
        ) != forbidden_pair


def test_parent_method_contract():
    sql = check_sql(
        SalesReturnCostRestorationEvent,
        "ck_srcre_event_valuation_method",
    )

    assert "'fifo'" in sql

    assert (
        "'weighted_average_moving'"
        in sql
    )


def test_parent_positive_and_nonnegative_checks():
    assert (
        check_sql(
            SalesReturnCostRestorationEvent,
            "ck_srcre_event_quantity_positive",
        )
        == "restored_quantity > 0"
    )

    assert (
        check_sql(
            SalesReturnCostRestorationEvent,
            "ck_srcre_event_valuation_nonnegative",
        )
        == "restored_valuation_amount >= 0"
    )

    assert (
        check_sql(
            SalesReturnCostRestorationEvent,
            "ck_srcre_event_cost_nonnegative",
        )
        == "restored_cost_amount >= 0"
    )

    assert (
        check_sql(
            SalesReturnCostRestorationEvent,
            "ck_srcre_event_unit_cost_nonnegative",
        )
        == (
            "aggregate_historical_unit_cost >= 0"
        )
    )


def test_parent_reversal_cannot_self_reference():
    sql = check_sql(
        SalesReturnCostRestorationEvent,
        "ck_srcre_event_not_self_reversal",
    )

    assert (
        "reversal_of_id IS NULL"
        in sql
    )

    assert (
        "reversal_of_id <> id"
        in sql
    )


def test_fifo_child_columns():
    columns = (
        SalesReturnCostRestorationFifoSlice
        .__table__
        .c
    )

    expected = {
        "id",
        "company_id",
        (
            "sales_return_cost_restoration_"
            "event_id"
        ),
        "fifo_consumption_id",
        "stock_lot_id",
        "restored_quantity",
        "historical_unit_cost",
        "restored_valuation_amount",
        "created_at",
    }

    assert set(
        columns.keys()
    ) == expected


def test_fifo_child_numeric_precision():
    columns = (
        SalesReturnCostRestorationFifoSlice
        .__table__
        .c
    )

    quantity = columns[
        "restored_quantity"
    ].type

    unit_cost = columns[
        "historical_unit_cost"
    ].type

    valuation = columns[
        "restored_valuation_amount"
    ].type

    assert (
        quantity.precision,
        quantity.scale,
    ) == (
        18,
        4,
    )

    assert (
        unit_cost.precision,
        unit_cost.scale,
    ) == (
        24,
        8,
    )

    assert (
        valuation.precision,
        valuation.scale,
    ) == (
        24,
        8,
    )


def test_fifo_child_parent_company_fk():
    fk = foreign_key(
        SalesReturnCostRestorationFifoSlice,
        "fk_srcfs_parent_event",
    )

    assert tuple(
        column.name
        for column
        in fk.columns
    ) == (
        "company_id",
        (
            "sales_return_cost_restoration_"
            "event_id"
        ),
    )

    assert tuple(
        str(
            element.column
        )
        for element
        in fk.elements
    ) == (
        (
            "sales_return_cost_restoration_events."
            "company_id"
        ),
        (
            "sales_return_cost_restoration_events."
            "id"
        ),
    )


def test_fifo_child_one_consumption_per_parent_event():
    sets = unique_sets(
        SalesReturnCostRestorationFifoSlice
    )

    assert (
        (
            "sales_return_cost_restoration_"
            "event_id"
        ),
        "fifo_consumption_id",
    ) in sets


def test_fifo_consumption_can_reappear_in_new_immutable_parent():
    """
    The schema intentionally does NOT make fifo_consumption_id
    globally unique.

    Original restoration, reversal and replacement may each
    snapshot the same historical FIFO source.
    """

    sets = unique_sets(
        SalesReturnCostRestorationFifoSlice
    )

    assert (
        "fifo_consumption_id",
    ) not in sets


def test_fifo_child_provenance_index():
    table = (
        SalesReturnCostRestorationFifoSlice
        .__table__
    )

    index = next(
        index
        for index
        in table.indexes
        if index.name
        == "ix_srcfs_fifo_provenance"
    )

    assert index.unique is False

    assert tuple(
        column.name
        for column
        in index.columns
    ) == (
        "company_id",
        "fifo_consumption_id",
        (
            "sales_return_cost_restoration_"
            "event_id"
        ),
    )


def test_fifo_child_checks():
    assert (
        check_sql(
            SalesReturnCostRestorationFifoSlice,
            "ck_srcfs_quantity_positive",
        )
        == "restored_quantity > 0"
    )

    assert (
        check_sql(
            SalesReturnCostRestorationFifoSlice,
            "ck_srcfs_unit_cost_nonnegative",
        )
        == "historical_unit_cost >= 0"
    )

    assert (
        check_sql(
            SalesReturnCostRestorationFifoSlice,
            "ck_srcfs_valuation_nonnegative",
        )
        == "restored_valuation_amount >= 0"
    )
