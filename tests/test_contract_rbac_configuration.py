from scripts.seed_rbac import PERMISSIONS
from scripts.seed_role_permissions import (
    ROLE_PERMISSIONS,
)


CONTRACT_PERMISSIONS = {
    "contracts.read",
    "contracts.create",
    "contracts.update",
}


def test_contract_permissions_registered() -> None:
    assert (
        CONTRACT_PERMISSIONS
        <= set(PERMISSIONS)
    )


def test_contract_full_access_roles() -> None:
    for role_name in (
        "admin",
        "director",
        "accountant",
        "manager",
    ):
        assert (
            CONTRACT_PERMISSIONS
            <= set(
                ROLE_PERMISSIONS[
                    role_name
                ]
            )
        )


def test_seller_can_read_contracts() -> None:
    assert (
        "contracts.read"
        in ROLE_PERMISSIONS["seller"]
    )


def test_seller_cannot_create_contracts() -> None:
    assert (
        "contracts.create"
        not in ROLE_PERMISSIONS["seller"]
    )


def test_seller_cannot_update_contracts() -> None:
    assert (
        "contracts.update"
        not in ROLE_PERMISSIONS["seller"]
    )
