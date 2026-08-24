from app.services.chart_of_accounts_template_types import (
    ChartOfAccountsTemplateType,
)
from app.services.ukrainian_working_subaccount_metadata import (
    validate_ukrainian_working_subaccount_metadata,
)
from app.services.ukrainian_working_system_account_catalog_builder import (
    build_ukrainian_working_system_account_catalog,
)


GENERAL_CHILD_PARENT = {
    "281": "28",
    "311": "31",
    "361": "36",
    "371": "37",
    "631": "63",
    "641": "64",
    "643": "64",
    "644": "64",
    "681": "68",
    "702": "70",
    "704": "70",
    "902": "90",
}


def test_working_subaccount_metadata_complete() -> None:
    validate_ukrainian_working_subaccount_metadata()


def test_general_working_catalog_count() -> None:
    catalog = (
        build_ukrainian_working_system_account_catalog(
            ChartOfAccountsTemplateType.GENERAL_291
        )
    )

    assert catalog.count == 96
    assert len(catalog.roots()) == 84


def test_general_working_hierarchy() -> None:
    catalog = (
        build_ukrainian_working_system_account_catalog(
            ChartOfAccountsTemplateType.GENERAL_291
        )
    )

    for child, parent in (
        GENERAL_CHILD_PARENT.items()
    ):
        assert (
            catalog.get(child).parent_code
            == parent
        )


def test_general_working_parent_postability() -> None:
    catalog = (
        build_ukrainian_working_system_account_catalog(
            ChartOfAccountsTemplateType.GENERAL_291
        )
    )

    parent_codes = set(
        GENERAL_CHILD_PARENT.values()
    )

    assert all(
        not catalog.get(code).is_postable
        for code in parent_codes
    )


def test_general_working_children_postable() -> None:
    catalog = (
        build_ukrainian_working_system_account_catalog(
            ChartOfAccountsTemplateType.GENERAL_291
        )
    )

    assert all(
        catalog.get(code).is_postable
        for code in GENERAL_CHILD_PARENT
    )


def test_general_parent_created_before_child() -> None:
    catalog = (
        build_ukrainian_working_system_account_catalog(
            ChartOfAccountsTemplateType.GENERAL_291
        )
    )

    seed_codes = [
        item.code
        for item in catalog.seed_order()
    ]

    for child, parent in (
        GENERAL_CHILD_PARENT.items()
    ):
        assert (
            seed_codes.index(parent)
            < seed_codes.index(child)
        )


def test_simplified_working_catalog_unchanged() -> None:
    catalog = (
        build_ukrainian_working_system_account_catalog(
            ChartOfAccountsTemplateType.SIMPLIFIED_186
        )
    )

    assert catalog.count == 30
    assert len(catalog.roots()) == 30

    assert catalog.get_or_none("281") is None
    assert catalog.get_or_none("361") is None
