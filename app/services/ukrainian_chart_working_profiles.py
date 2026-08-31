from types import MappingProxyType

from app.services.accounting_account_roles import (
    AccountingAccountRole,
)
from app.services.chart_of_accounts_template_types import (
    ChartOfAccountsTemplateType,
)
from app.services.chart_of_accounts_working_profile import (
    ChartOfAccountsWorkingProfile,
)
from app.services.ukrainian_chart_general_291_working_subaccounts_source import (
    GENERAL_291_WORKING_SUBACCOUNTS_V1,
)
from app.services.ukrainian_chart_simplified_186_working_subaccounts_source import (
    SIMPLIFIED_186_WORKING_SUBACCOUNTS_V1,
)
from app.services.ukrainian_chart_source_registry import (
    get_ukrainian_synthetic_account_source,
)


GENERAL_291_WORKING_PROFILE = (
    ChartOfAccountsWorkingProfile(
        template_type=(
            ChartOfAccountsTemplateType.GENERAL_291
        ),
        role_to_code={
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
        },
    )
)


SIMPLIFIED_186_WORKING_PROFILE = (
    ChartOfAccountsWorkingProfile(
        template_type=(
            ChartOfAccountsTemplateType.SIMPLIFIED_186
        ),
        role_to_code={
            AccountingAccountRole.INVENTORY_GOODS:
                "26",
            AccountingAccountRole.GOODS_COGS:
                "90",
        },
    )
)


UKRAINIAN_CHART_WORKING_PROFILE_REGISTRY = (
    MappingProxyType(
        {
            ChartOfAccountsTemplateType.GENERAL_291:
                GENERAL_291_WORKING_PROFILE,

            ChartOfAccountsTemplateType.SIMPLIFIED_186:
                SIMPLIFIED_186_WORKING_PROFILE,
        }
    )
)


def get_ukrainian_chart_working_profile(
    template_type: ChartOfAccountsTemplateType,
) -> ChartOfAccountsWorkingProfile:
    try:
        return (
            UKRAINIAN_CHART_WORKING_PROFILE_REGISTRY[
                template_type
            ]
        )
    except KeyError as exc:
        raise ValueError(
            (
                "Unsupported Ukrainian Chart of "
                f"Accounts template: {template_type!r}"
            )
        ) from exc


def _available_account_codes(
    template_type: ChartOfAccountsTemplateType,
) -> set[str]:
    synthetic_source = (
        get_ukrainian_synthetic_account_source(
            template_type
        )
    )

    if (
        template_type
        == ChartOfAccountsTemplateType.GENERAL_291
    ):
        working_source = (
            GENERAL_291_WORKING_SUBACCOUNTS_V1
        )
    elif (
        template_type
        == ChartOfAccountsTemplateType.SIMPLIFIED_186
    ):
        working_source = (
            SIMPLIFIED_186_WORKING_SUBACCOUNTS_V1
        )
    else:
        raise ValueError(
            (
                "Unsupported Ukrainian Chart of "
                f"Accounts template: {template_type!r}"
            )
        )

    return {
        item.code
        for item in (
            *synthetic_source,
            *working_source,
        )
    }


def validate_ukrainian_chart_working_profiles(
) -> None:
    expected_templates = set(
        ChartOfAccountsTemplateType
    )

    registered_templates = set(
        UKRAINIAN_CHART_WORKING_PROFILE_REGISTRY
    )

    if registered_templates != expected_templates:
        missing = (
            expected_templates
            - registered_templates
        )

        extra = (
            registered_templates
            - expected_templates
        )

        raise ValueError(
            (
                "Working profile registry mismatch. "
                f"Missing={sorted(missing)}, "
                f"extra={sorted(extra)}"
            )
        )

    for (
        template_type,
        profile,
    ) in (
        UKRAINIAN_CHART_WORKING_PROFILE_REGISTRY.items()
    ):
        if profile.template_type != template_type:
            raise ValueError(
                (
                    "Working profile template mismatch: "
                    f"{template_type.value}"
                )
            )

        available_codes = (
            _available_account_codes(
                template_type
            )
        )

        invalid_mappings = {
            role.value: code
            for (
                role,
                code,
            ) in profile.role_to_code.items()
            if code not in available_codes
        }

        if invalid_mappings:
            raise ValueError(
                (
                    "Working profile contains account "
                    "codes absent from its chart source: "
                    f"{invalid_mappings}"
                )
            )


validate_ukrainian_chart_working_profiles()
