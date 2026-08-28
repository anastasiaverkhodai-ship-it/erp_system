from datetime import date
from pathlib import Path

import pytest

from app.main import app
from app.schemas.trade_document import (
    SalesOrderFulfillmentReversalRequest,
    SalesOrderFulfillmentReversalResponse,
)


BASE_PATH = (
    "/api/v1/companies/"
    "{company_id}/trade-documents/"
    "{document_id}/fulfillments/"
    "{fulfillment_id}/reverse"
)

PERMISSION = (
    "trade_documents.fulfillments.reverse"
)

ALLOWED_ROLES = (
    "admin",
    "director",
    "accountant",
    "manager",
)


def test_reversal_request_schema():
    data = (
        SalesOrderFulfillmentReversalRequest(
            reversal_date=date(
                2026,
                8,
                28,
            )
        )
    )

    assert (
        data.reversal_date
        == date(
            2026,
            8,
            28,
        )
    )


def test_reversal_request_forbids_extra_fields():
    with pytest.raises(
        ValueError
    ):
        SalesOrderFulfillmentReversalRequest(
            reversal_date=date(
                2026,
                8,
                28,
            ),
            accounting_rule_id=5,
        )


def test_reversal_response_contract():
    fields = set(
        SalesOrderFulfillmentReversalResponse
        .model_fields
    )

    assert fields == {
        "trade_document",
        "warehouse_document_id",
        "fulfillment_id",
    }


def test_reversal_route_exists():
    schema = app.openapi()

    assert BASE_PATH in schema["paths"]

    operation = schema[
        "paths"
    ][BASE_PATH]

    assert "post" in operation

    assert (
        operation["post"]["responses"]["200"]
        ["content"]["application/json"]
        ["schema"]["$ref"]
        .endswith(
            "/SalesOrderFulfillmentReversalResponse"
        )
    )


def test_permission_registered():
    text = Path(
        "scripts/seed_rbac.py"
    ).read_text()

    assert (
        f'"{PERMISSION}"'
        in text
    )


@pytest.mark.parametrize(
    "role_name",
    ALLOWED_ROLES,
)
def test_allowed_roles_have_reversal_permission(
    role_name,
):
    namespace = {}

    exec(
        Path(
            "scripts/seed_role_permissions.py"
        ).read_text(),
        namespace,
    )

    permissions = namespace[
        "ROLE_PERMISSIONS"
    ][role_name]

    assert PERMISSION in permissions


def test_seller_cannot_reverse_fulfillment():
    namespace = {}

    exec(
        Path(
            "scripts/seed_role_permissions.py"
        ).read_text(),
        namespace,
    )

    seller_permissions = namespace[
        "ROLE_PERMISSIONS"
    ]["seller"]

    assert (
        PERMISSION
        not in seller_permissions
    )
