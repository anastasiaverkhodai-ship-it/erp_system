import pytest

from app.services.accounting_account_roles import (
    AccountingAccountRole,
)
from app.services.chart_of_accounts_template_types import (
    ChartOfAccountsTemplateType,
)
from app.services.chart_of_accounts_working_profile import (
    WorkingAccountRoleNotConfiguredError,
)
from app.services.ukrainian_chart_working_profiles import (
    UKRAINIAN_CHART_WORKING_PROFILE_REGISTRY,
    get_ukrainian_chart_working_profile,
    validate_ukrainian_chart_working_profiles,
)


def test_working_profile_registry_complete() -> None:
    assert set(
        UKRAINIAN_CHART_WORKING_PROFILE_REGISTRY
    ) == set(
        ChartOfAccountsTemplateType
    )


def test_general_291_working_profile_count() -> None:
    profile = get_ukrainian_chart_working_profile(
        ChartOfAccountsTemplateType.GENERAL_291
    )

    assert profile.count == 12


def test_general_291_working_profile_mapping() -> None:
    profile = get_ukrainian_chart_working_profile(
        ChartOfAccountsTemplateType.GENERAL_291
    )

    expected = {
        AccountingAccountRole.INVENTORY_GOODS:
            "281",
        AccountingAccountRole.BANK_CURRENT_UAH:
            "311",
        AccountingAccountRole.CUSTOMER_RECEIVABLES:
            "361",
        AccountingAccountRole.SUPPLIER_ADVANCES:
            "371",
        AccountingAccountRole.SUPPLIER_PAYABLES:
            "631",
        AccountingAccountRole.TAX_SETTLEMENT:
            "641",
        AccountingAccountRole.VAT_OUTPUT:
            "643",
        AccountingAccountRole.VAT_INPUT:
            "644",
        AccountingAccountRole.CUSTOMER_ADVANCES:
            "681",
        AccountingAccountRole.GOODS_REVENUE:
            "702",
        AccountingAccountRole.SALES_DEDUCTIONS:
            "704",
        AccountingAccountRole.GOODS_COGS:
            "902",
    }

    assert dict(
        profile.role_to_code
    ) == expected


def test_simplified_186_inventory_roles() -> None:
    profile = get_ukrainian_chart_working_profile(
        ChartOfAccountsTemplateType.SIMPLIFIED_186
    )

    assert profile.count == 2

    assert dict(
        profile.role_to_code
    ) == {
        AccountingAccountRole.INVENTORY_GOODS:
            "26",
        AccountingAccountRole.GOODS_COGS:
            "90",
    }


def test_simplified_missing_role_rejected() -> None:
    profile = get_ukrainian_chart_working_profile(
        ChartOfAccountsTemplateType.SIMPLIFIED_186
    )

    with pytest.raises(
        WorkingAccountRoleNotConfiguredError
    ):
        profile.get_code(
            AccountingAccountRole.GOODS_REVENUE
        )


def test_working_profile_is_immutable() -> None:
    profile = get_ukrainian_chart_working_profile(
        ChartOfAccountsTemplateType.GENERAL_291
    )

    with pytest.raises(TypeError):
        profile.role_to_code[
            AccountingAccountRole.GOODS_REVENUE
        ] = "999"


def test_working_profiles_validate() -> None:
    validate_ukrainian_chart_working_profiles()
