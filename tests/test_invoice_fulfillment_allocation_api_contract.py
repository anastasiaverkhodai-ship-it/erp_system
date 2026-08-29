from pathlib import Path

import pytest

from app.main import app

from scripts.seed_rbac import (
    PERMISSIONS,
)
from scripts.seed_role_permissions import (
    ROLE_PERMISSIONS,
)


BASE = (
    "/api/v1/companies/"
    "{company_id}/trade-documents/"
    "{invoice_id}"
)

ALLOCATION_PATH = (
    BASE
    + "/fulfillment-allocations"
)

ALLOCATION_REVERSE_PATH = (
    ALLOCATION_PATH
    + "/{allocation_id}/reverse"
)

RECONCILIATION_PATH = (
    BASE
    + "/reconciliation"
)

READ_PERMISSION = (
    "trade_documents.allocations.read"
)

MANAGE_PERMISSION = (
    "trade_documents.allocations.manage"
)


def paths():
    return app.openapi()[
        "paths"
    ]


def test_allocation_collection_routes():
    schema = paths()

    assert ALLOCATION_PATH in schema

    assert {
        "get",
        "post",
    } <= set(
        schema[
            ALLOCATION_PATH
        ]
    )


def test_allocation_reverse_route():
    schema = paths()

    assert (
        ALLOCATION_REVERSE_PATH
        in schema
    )

    assert (
        "post"
        in schema[
            ALLOCATION_REVERSE_PATH
        ]
    )


def test_reconciliation_route():
    schema = paths()

    assert (
        RECONCILIATION_PATH
        in schema
    )

    assert (
        "get"
        in schema[
            RECONCILIATION_PATH
        ]
    )


def test_no_allocation_hard_delete():
    schema = paths()

    assert (
        "delete"
        not in schema[
            ALLOCATION_PATH
        ]
    )

    assert (
        "delete"
        not in schema[
            ALLOCATION_REVERSE_PATH
        ]
    )


def test_create_response_contract():
    operation = paths()[
        ALLOCATION_PATH
    ]["post"]

    ref = (
        operation[
            "responses"
        ]["200"][
            "content"
        ]["application/json"][
            "schema"
        ]["$ref"]
    )

    assert ref.endswith(
        "/InvoiceFulfillmentAllocationResponse"
    )


def test_reverse_response_contract():
    operation = paths()[
        ALLOCATION_REVERSE_PATH
    ]["post"]

    ref = (
        operation[
            "responses"
        ]["200"][
            "content"
        ]["application/json"][
            "schema"
        ]["$ref"]
    )

    assert ref.endswith(
        "/InvoiceFulfillmentAllocationResponse"
    )


def test_reconciliation_response_contract():
    operation = paths()[
        RECONCILIATION_PATH
    ]["get"]

    ref = (
        operation[
            "responses"
        ]["200"][
            "content"
        ]["application/json"][
            "schema"
        ]["$ref"]
    )

    assert ref.endswith(
        "/InvoiceFulfillmentReconciliationResponse"
    )


def test_permissions_registered():
    assert (
        READ_PERMISSION
        in PERMISSIONS
    )

    assert (
        MANAGE_PERMISSION
        in PERMISSIONS
    )


@pytest.mark.parametrize(
    "role_name",
    [
        "admin",
        "director",
        "accountant",
        "manager",
        "seller",
    ],
)
def test_read_permission_roles(
    role_name,
):
    assert (
        READ_PERMISSION
        in ROLE_PERMISSIONS[
            role_name
        ]
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
def test_manage_permission_roles(
    role_name,
):
    assert (
        MANAGE_PERMISSION
        in ROLE_PERMISSIONS[
            role_name
        ]
    )


def test_seller_cannot_manage_allocations():
    assert (
        MANAGE_PERMISSION
        not in ROLE_PERMISSIONS[
            "seller"
        ]
    )


def test_router_contains_permission_dependencies():
    text = Path(
        "app/api/v1/trade_documents.py"
    ).read_text()

    assert (
        '"trade_documents.allocations.read"'
        in text
    )

    assert (
        '"trade_documents.allocations.manage"'
        in text
    )
