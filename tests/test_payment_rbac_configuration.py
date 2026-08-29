from scripts.seed_rbac import (
    PERMISSIONS,
)
from scripts.seed_role_permissions import (
    ROLE_PERMISSIONS,
)


READ = "payments.read"
MANAGE = "payments.manage"

SETTLEMENT_READ = (
    "payments.settlements.read"
)

SETTLEMENT_MANAGE = (
    "payments.settlements.manage"
)


def test_payment_permissions_registered():
    assert {
        READ,
        MANAGE,
        SETTLEMENT_READ,
        SETTLEMENT_MANAGE,
    } <= set(
        PERMISSIONS
    )


def test_admin_director_accountant_manage_payments():
    for role in (
        "admin",
        "director",
        "accountant",
    ):
        assert {
            READ,
            MANAGE,
            SETTLEMENT_READ,
            SETTLEMENT_MANAGE,
        } <= set(
            ROLE_PERMISSIONS[role]
        )


def test_manager_can_read_but_not_manage_payments():
    permissions = set(
        ROLE_PERMISSIONS["manager"]
    )

    assert READ in permissions

    assert (
        SETTLEMENT_READ
        in permissions
    )

    assert MANAGE not in permissions

    assert (
        SETTLEMENT_MANAGE
        not in permissions
    )


def test_seller_has_no_payment_permissions():
    permissions = set(
        ROLE_PERMISSIONS["seller"]
    )

    assert not {
        READ,
        MANAGE,
        SETTLEMENT_READ,
        SETTLEMENT_MANAGE,
    } & permissions
