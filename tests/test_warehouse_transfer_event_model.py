from decimal import Decimal

from sqlalchemy import CheckConstraint, ForeignKeyConstraint

from app.models.document import DocumentType
from app.models.warehouse_transfer_event import (
    WarehouseTransferEvent,
)
from app.models.warehouse_transfer_line import (
    WarehouseTransferLine,
)


def _constraint_names(model, kind):
    return {
        constraint.name
        for constraint in model.__table__.constraints
        if isinstance(constraint, kind)
        and constraint.name is not None
    }


def test_transfer_event_table_contract():
    assert set(
        WarehouseTransferEvent.__table__.columns.keys()
    ) == {
        "id",
        "company_id",
        "history_key",
        "source_warehouse_id",
        "destination_warehouse_id",
        "transfer_date",
        "created_by",
        "reversal_of_id",
        "created_at",
    }


def test_transfer_event_company_scoped_fks_exist():
    names = _constraint_names(
        WarehouseTransferEvent,
        ForeignKeyConstraint,
    )

    assert "fk_wte_company_source_warehouse" in names
    assert "fk_wte_company_destination_warehouse" in names
    assert "fk_wte_history_reversal_of" in names


def test_transfer_line_is_paired_issue_receipt_contract():
    assert set(
        WarehouseTransferLine.__table__.columns.keys()
    ) == {
        "id",
        "company_id",
        "transfer_event_id",
        "product_id",
        "source_warehouse_id",
        "destination_warehouse_id",
        "quantity",
        "issue_document_id",
        "issue_document_line_id",
        "issue_document_type",
        "receipt_document_id",
        "receipt_document_type",
        "created_at",
    }


def test_transfer_line_exact_fk_contract():
    names = _constraint_names(
        WarehouseTransferLine,
        ForeignKeyConstraint,
    )

    assert names == {
        "fk_wtl_transfer_parent_identity",
        "fk_wtl_company_issue_document",
        "fk_wtl_company_receipt_document",
        "fk_wtl_issue_document_line",
    }


def test_transfer_line_type_checks_exist():
    names = _constraint_names(
        WarehouseTransferLine,
        CheckConstraint,
    )

    assert {
        "ck_wtl_quantity_positive",
        "ck_wtl_distinct_warehouses",
        "ck_wtl_issue_document_type",
        "ck_wtl_receipt_document_type",
        "ck_wtl_distinct_documents",
    } <= names


def test_transfer_line_python_contract():
    line = WarehouseTransferLine(
        company_id=1,
        transfer_event_id=2,
        product_id=3,
        source_warehouse_id=4,
        destination_warehouse_id=5,
        quantity=Decimal("7.5000"),
        issue_document_id=10,
        issue_document_line_id=11,
        issue_document_type=DocumentType.ISSUE,
        receipt_document_id=20,
        receipt_document_type=DocumentType.RECEIPT,
    )

    assert line.quantity == Decimal("7.5000")
    assert line.issue_document_type == DocumentType.ISSUE
    assert line.receipt_document_type == DocumentType.RECEIPT
