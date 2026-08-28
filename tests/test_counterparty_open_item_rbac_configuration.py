from scripts.seed_rbac import PERMISSIONS
from scripts.seed_role_permissions import (
    ROLE_PERMISSIONS,
)


PERMISSION = (
    "counterparty_open_items.read"
)


def test_open_item_permission_registered():
    assert PERMISSION in PERMISSIONS


def test_financial_roles_can_read_open_items():
    for role in (
        "admin",
        "director",
        "accountant",
        "manager",
    ):
        assert (
            PERMISSION
            in ROLE_PERMISSIONS[role]
        )


def test_seller_cannot_read_open_items():
    assert (
        PERMISSION
        not in ROLE_PERMISSIONS["seller"]
    )
