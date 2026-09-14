from pathlib import Path

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.api.v1.products import (
    _normalize_base_uom_code,
)
from app.models.product import Product
from app.schemas.product import (
    ProductCreate,
    ProductResponse,
    ProductUpdate,
)


def test_product_model_has_nullable_legacy_base_uom():
    column = Product.__table__.c.base_uom_code

    assert column.nullable is True
    assert column.type.length == 20


def test_product_create_requires_base_uom():
    with pytest.raises(ValidationError):
        ProductCreate(
            name="Product",
            sku="SKU-1",
        )


def test_product_create_accepts_base_uom():
    value = ProductCreate(
        name="Product",
        sku="SKU-1",
        base_uom_code="PCS",
    )

    assert value.base_uom_code == "PCS"


def test_product_update_allows_base_uom_assignment():
    value = ProductUpdate(
        base_uom_code="PCS",
    )

    assert value.base_uom_code == "PCS"


def test_product_update_does_not_require_base_uom():
    value = ProductUpdate(
        name="Updated",
    )

    assert value.base_uom_code is None


def test_product_response_allows_legacy_null_uom():
    value = ProductResponse(
        id=1,
        company_id=1,
        name="Legacy",
        sku="LEGACY",
        base_uom_code=None,
        is_active=True,
    )

    assert value.base_uom_code is None


def test_base_uom_normalizes_to_catalog_code():
    assert (
        _normalize_base_uom_code(" PCS ")
        == "pcs"
    )


def test_unknown_base_uom_fails_closed():
    with pytest.raises(HTTPException) as exc:
        _normalize_base_uom_code(
            "NOT-A-UOM"
        )

    assert exc.value.status_code == 422


def test_migration_does_not_guess_legacy_uom():
    text = Path(
        "alembic/versions/"
        "d4f2a7c9e613_add_product_base_uom.py"
    ).read_text(encoding="utf-8")

    assert "UPDATE products" not in text
    assert 'server_default="PCS"' not in text
    assert "nullable=True" in text


def test_trade_line_not_changed_by_base_uom_patch():
    text = Path(
        "app/models/trade_document_line.py"
    ).read_text(encoding="utf-8")

    assert "base_uom_code" not in text


def test_pricing_master_uom_normalizer_uses_system_catalog():
    from app.services.pricing_master_service import (
        _normalize_uom,
    )

    assert (
        _normalize_uom(" PCS ")
        == "pcs"
    )


def test_pricing_master_unknown_uom_fails_closed():
    from app.services.pricing_master_service import (
        PricingMasterError,
        _normalize_uom,
    )

    with pytest.raises(PricingMasterError):
        _normalize_uom(
            "NOT-A-UOM"
        )
