import ast
import inspect

from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
    UniqueConstraint,
)

from app.models.purchase_return_recognition_event import (
    PurchaseReturnRecognitionEvent,
)


def test_columns():
    assert tuple(
        column.name
        for column
        in PurchaseReturnRecognitionEvent
        .__table__.columns
    ) == (
        "id",
        "company_id",
        "trade_return_event_id",
        "invoice_fulfillment_allocation_id",
        "recognition_date",
        "returned_quantity",
        "returned_base_amount",
        "returned_gross_amount",
        "returned_tax_amount",
        "currency_code",
        "created_by",
        "created_at",
        "reversal_of_id",
    )


def test_named_source_foreign_keys():
    names = {
        constraint.name
        for constraint
        in PurchaseReturnRecognitionEvent
        .__table__.constraints
        if isinstance(
            constraint,
            ForeignKeyConstraint,
        )
        and constraint.name is not None
    }

    assert {
        "fk_prre_event_company_trade_return",
        (
            "fk_prre_event_company_"
            "invoice_fulfillment_allocation"
        ),
        "fk_prre_event_reversal_source",
    }.issubset(
        names
    )


def test_invoice_fulfillment_allocation_fk():
    table = (
        PurchaseReturnRecognitionEvent
        .__table__
    )

    constraint = next(
        constraint
        for constraint
        in table.constraints
        if (
            isinstance(
                constraint,
                ForeignKeyConstraint,
            )
            and constraint.name
            == (
                "fk_prre_event_company_"
                "invoice_fulfillment_allocation"
            )
        )
    )

    assert tuple(
        column.name
        for column
        in constraint.columns
    ) == (
        "company_id",
        "invoice_fulfillment_allocation_id",
    )

    assert tuple(
        element.target_fullname
        for element
        in constraint.elements
    ) == (
        (
            "invoice_fulfillment_allocations."
            "company_id"
        ),
        (
            "invoice_fulfillment_allocations."
            "id"
        ),
    )

    assert constraint.ondelete == "RESTRICT"


def test_reversal_fk_preserves_exact_provenance():
    table = (
        PurchaseReturnRecognitionEvent
        .__table__
    )

    constraint = next(
        constraint
        for constraint
        in table.constraints
        if (
            isinstance(
                constraint,
                ForeignKeyConstraint,
            )
            and constraint.name
            == "fk_prre_event_reversal_source"
        )
    )

    assert tuple(
        column.name
        for column
        in constraint.columns
    ) == (
        "company_id",
        "reversal_of_id",
        "trade_return_event_id",
        "invoice_fulfillment_allocation_id",
    )

    assert tuple(
        element.target_fullname
        for element
        in constraint.elements
    ) == (
        (
            "purchase_return_recognition_events."
            "company_id"
        ),
        (
            "purchase_return_recognition_events."
            "id"
        ),
        (
            "purchase_return_recognition_events."
            "trade_return_event_id"
        ),
        (
            "purchase_return_recognition_events."
            "invoice_fulfillment_allocation_id"
        ),
    )


def test_business_checks():
    names = {
        constraint.name
        for constraint
        in PurchaseReturnRecognitionEvent
        .__table__.constraints
        if isinstance(
            constraint,
            CheckConstraint,
        )
    }

    assert {
        (
            "ck_prre_event_"
            "returned_quantity_positive"
        ),
        (
            "ck_prre_event_"
            "returned_base_nonnegative"
        ),
        (
            "ck_prre_event_"
            "returned_gross_nonnegative"
        ),
        (
            "ck_prre_event_"
            "returned_tax_nonnegative"
        ),
        (
            "ck_prre_event_"
            "tax_not_above_gross"
        ),
        (
            "ck_prre_event_"
            "currency_length"
        ),
        (
            "ck_prre_event_"
            "not_self_reversal"
        ),
    }.issubset(
        names
    )


def test_rounding_sensitive_monetary_checks_allow_zero():
    checks = {
        constraint.name: " ".join(
            str(
                constraint.sqltext
            ).split()
        )
        for constraint
        in PurchaseReturnRecognitionEvent
        .__table__.constraints
        if isinstance(
            constraint,
            CheckConstraint,
        )
    }

    assert (
        checks[
            "ck_prre_event_returned_base_nonnegative"
        ]
        == "returned_base_amount >= 0"
    )

    assert (
        checks[
            "ck_prre_event_returned_gross_nonnegative"
        ]
        == "returned_gross_amount >= 0"
    )

    assert (
        checks[
            "ck_prre_event_returned_tax_nonnegative"
        ]
        == "returned_tax_amount >= 0"
    )


def test_base_is_not_derived_from_gross_minus_tax():
    source = inspect.getsource(
        PurchaseReturnRecognitionEvent
    )
    tree = ast.parse(source)

    forbidden_subtractions = []

    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.BinOp)
            and isinstance(node.op, ast.Sub)
        ):
            continue

        if (
            isinstance(node.left, ast.Name)
            and node.left.id == "returned_gross_amount"
            and isinstance(node.right, ast.Name)
            and node.right.id == "returned_tax_amount"
        ):
            forbidden_subtractions.append(
                node
            )

    assert forbidden_subtractions == []

    checks = tuple(
        str(
            constraint.sqltext
        )
        for constraint
        in PurchaseReturnRecognitionEvent
        .__table__.constraints
        if isinstance(
            constraint,
            CheckConstraint,
        )
    )

    assert all(
        (
            "returned_gross_amount - "
            "returned_tax_amount"
        )
        not in check
        for check in checks
    )


def test_reversal_is_unique():
    assert any(
        isinstance(
            constraint,
            UniqueConstraint,
        )
        and constraint.name
        == "uq_prre_event_reversal_of"
        for constraint
        in PurchaseReturnRecognitionEvent
        .__table__.constraints
    )


def test_pair_history_index_allows_immutable_replacement():
    index = next(
        index
        for index
        in PurchaseReturnRecognitionEvent
        .__table__.indexes
        if index.name
        == "ix_prre_event_pair_history"
    )

    assert index.unique is False

    assert tuple(
        column.name
        for column
        in index.columns
    ) == (
        "company_id",
        "trade_return_event_id",
        "invoice_fulfillment_allocation_id",
        "id",
    )

    assert (
        index.dialect_options[
            "postgresql"
        ].get(
            "where"
        )
        is None
    )
