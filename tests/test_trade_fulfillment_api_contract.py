from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.api.v1.trade_documents import router
from app.schemas.trade_document import (
    SalesOrderFulfillmentLineRequest,
    SalesOrderFulfillmentRequest,
    SalesOrderFulfillmentResponse,
)
from scripts.seed_rbac import PERMISSIONS
from scripts.seed_role_permissions import (
    ROLE_PERMISSIONS,
)


FULFILL_PERMISSION = (
    "trade_documents.fulfill"
)


def test_fulfillment_request_schema():
    data = SalesOrderFulfillmentRequest(
        warehouse_document_number=(
            "  ISSUE-2026-0001  "
        ),
        document_date=date(
            2026,
            8,
            27,
        ),
        accounting_rule_id=5,
        lines=[
            SalesOrderFulfillmentLineRequest(
                trade_document_line_id=10,
                quantity=Decimal("2.0000"),
            )
        ],
    )

    assert (
        data.warehouse_document_number
        == "ISSUE-2026-0001"
    )

    assert (
        data.accounting_rule_id
        == 5
    )

    assert (
        data.lines[0].quantity
        == Decimal("2.0000")
    )


def test_fulfillment_line_rejects_product_override():
    with pytest.raises(ValidationError):
        SalesOrderFulfillmentLineRequest(
            trade_document_line_id=10,
            quantity=Decimal("2.0000"),
            product_id=999,
        )


def test_fulfillment_line_rejects_warehouse_override():
    with pytest.raises(ValidationError):
        SalesOrderFulfillmentLineRequest(
            trade_document_line_id=10,
            quantity=Decimal("2.0000"),
            warehouse_id=999,
        )


def test_fulfillment_request_requires_lines():
    with pytest.raises(ValidationError):
        SalesOrderFulfillmentRequest(
            warehouse_document_number="ISSUE-1",
            document_date=date(
                2026,
                8,
                27,
            ),
            accounting_rule_id=5,
            lines=[],
        )


def test_fulfill_route_exists():
    expected_path = (
        "/companies/{company_id}"
        "/trade-documents/{document_id}"
        "/fulfill"
    )

    matching_routes = [
        route
        for route in router.routes
        if getattr(
            route,
            "path",
            None,
        ) == expected_path
    ]

    assert len(matching_routes) == 1

    route = matching_routes[0]

    assert route.methods == {
        "POST",
    }

    assert (
        route.response_model
        is SalesOrderFulfillmentResponse
    )


def test_fulfillment_permission_is_registered():
    assert (
        FULFILL_PERMISSION
        in PERMISSIONS
    )


@pytest.mark.parametrize(
    "role_name",
    [
        "admin",
        "director",
        "accountant",
        "manager",
    ],
)
def test_fulfillment_permission_allowed_roles(
    role_name,
):
    assert (
        FULFILL_PERMISSION
        in ROLE_PERMISSIONS[
            role_name
        ]
    )


def test_seller_cannot_fulfill_sales_order():
    assert (
        FULFILL_PERMISSION
        not in ROLE_PERMISSIONS[
            "seller"
        ]
    )
