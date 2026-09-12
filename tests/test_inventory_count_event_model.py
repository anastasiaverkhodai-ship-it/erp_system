from datetime import date
from decimal import Decimal

from sqlalchemy import CheckConstraint, ForeignKeyConstraint

from app.models.document import DocumentType
from app.models.inventory_count_event import InventoryCountEvent
from app.models.inventory_count_variance_event import (
    InventoryCountVarianceDirection,
    InventoryCountVarianceEvent,
)


def _constraint_names(model, kind):
    return {
        constraint.name
        for constraint in model.__table__.constraints
        if isinstance(constraint, kind)
        and constraint.name is not None
    }


def test_count_snapshot_contract():
    assert set(
        InventoryCountEvent.__table__.columns.keys()
    ) == {
        "id",
        "company_id",
        "history_key",
        "product_id",
        "warehouse_id",
        "count_date",
        "expected_quantity",
        "counted_quantity",
        "created_by",
        "reversal_of_id",
        "created_at",
    }


def test_count_snapshot_company_stock_identity_fks():
    names = _constraint_names(
        InventoryCountEvent,
        ForeignKeyConstraint,
    )

    assert "fk_icev_company_product" in names
    assert "fk_icev_company_warehouse" in names
    assert "fk_icev_history_reversal_of" in names


def test_count_variance_property():
    event = InventoryCountEvent(
        company_id=1,
        product_id=2,
        warehouse_id=3,
        count_date=date(2026, 9, 10),
        expected_quantity=Decimal("10"),
        counted_quantity=Decimal("7.5"),
        created_by=4,
    )

    assert event.variance_quantity == Decimal("-2.5")


def test_variance_exact_document_provenance_contract():
    names = _constraint_names(
        InventoryCountVarianceEvent,
        ForeignKeyConstraint,
    )

    assert names == {
        "fk_icve_count_stock_identity",
        "fk_icve_company_adjustment_document",
        "fk_icve_adjustment_document_line",
    }


def test_variance_adjustment_type_check_exists():
    names = _constraint_names(
        InventoryCountVarianceEvent,
        CheckConstraint,
    )

    assert "ck_icve_document_type_adjustment" in names


def test_variance_signed_quantity():
    increase = InventoryCountVarianceEvent(
        company_id=1,
        inventory_count_event_id=2,
        document_id=3,
        document_line_id=4,
        document_type=DocumentType.ADJUSTMENT,
        product_id=5,
        warehouse_id=6,
        adjustment_date=date(2026, 9, 10),
        direction=InventoryCountVarianceDirection.INCREASE,
        quantity=Decimal("2"),
    )

    decrease = InventoryCountVarianceEvent(
        company_id=1,
        inventory_count_event_id=2,
        document_id=3,
        document_line_id=4,
        document_type=DocumentType.ADJUSTMENT,
        product_id=5,
        warehouse_id=6,
        adjustment_date=date(2026, 9, 10),
        direction=InventoryCountVarianceDirection.DECREASE,
        quantity=Decimal("2"),
    )

    assert increase.signed_quantity == Decimal("2")
    assert decrease.signed_quantity == Decimal("-2")
