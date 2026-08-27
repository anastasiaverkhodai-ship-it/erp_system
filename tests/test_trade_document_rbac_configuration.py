from scripts.seed_rbac import PERMISSIONS
from scripts.seed_role_permissions import (
    ROLE_PERMISSIONS,
)


TRADE_PERMISSIONS = {
    "trade_documents.read",
    "trade_documents.create",
    "trade_documents.update",
    "trade_documents.confirm",
    "trade_documents.cancel",
}


def test_trade_document_permissions_registered() -> None:
    assert (
        TRADE_PERMISSIONS
        <= set(PERMISSIONS)
    )


def test_admin_trade_document_permissions() -> None:
    assert TRADE_PERMISSIONS <= set(
        ROLE_PERMISSIONS["admin"]
    )


def test_director_trade_document_permissions() -> None:
    assert TRADE_PERMISSIONS <= set(
        ROLE_PERMISSIONS["director"]
    )


def test_accountant_trade_document_permissions() -> None:
    assert TRADE_PERMISSIONS <= set(
        ROLE_PERMISSIONS["accountant"]
    )


def test_manager_trade_document_permissions() -> None:
    assert TRADE_PERMISSIONS <= set(
        ROLE_PERMISSIONS["manager"]
    )


def test_seller_trade_document_permissions() -> None:
    permissions = set(
        ROLE_PERMISSIONS["seller"]
    )

    assert (
        "trade_documents.read"
        in permissions
    )

    assert (
        "trade_documents.create"
        not in permissions
    )

    assert (
        "trade_documents.update"
        not in permissions
    )

    assert (
        "trade_documents.confirm"
        not in permissions
    )

    assert (
        "trade_documents.cancel"
        not in permissions
    )
