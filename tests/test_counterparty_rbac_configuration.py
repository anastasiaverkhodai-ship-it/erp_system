from scripts.seed_rbac import PERMISSIONS
from scripts.seed_role_permissions import (
    ROLE_PERMISSIONS,
)


COUNTERPARTY_PERMISSIONS = {
    "counterparties.read",
    "counterparties.create",
    "counterparties.update",
}


def test_counterparty_permissions_registered() -> None:
    assert (
        COUNTERPARTY_PERMISSIONS
        <= set(PERMISSIONS)
    )


def test_counterparty_full_access_roles() -> None:
    for role_name in (
        "admin",
        "director",
        "accountant",
        "manager",
    ):
        assert (
            COUNTERPARTY_PERMISSIONS
            <= set(
                ROLE_PERMISSIONS[
                    role_name
                ]
            )
        )


def test_seller_can_read_counterparties() -> None:
    assert (
        "counterparties.read"
        in ROLE_PERMISSIONS["seller"]
    )


def test_seller_can_create_counterparties() -> None:
    assert (
        "counterparties.create"
        in ROLE_PERMISSIONS["seller"]
    )


def test_seller_cannot_update_counterparties() -> None:
    assert (
        "counterparties.update"
        not in ROLE_PERMISSIONS["seller"]
    )
