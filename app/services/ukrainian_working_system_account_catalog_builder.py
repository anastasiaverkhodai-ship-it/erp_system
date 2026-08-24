from app.services.chart_of_accounts_template_types import (
    ChartOfAccountsTemplateType,
)
from app.services.system_account_catalog import (
    SystemAccountCatalog,
)
from app.services.system_account_definition import (
    SystemAccountDefinition,
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
from app.services.ukrainian_chart_synthetic_metadata import (
    get_ukrainian_synthetic_account_metadata,
)
from app.services.ukrainian_working_subaccount_metadata import (
    get_ukrainian_working_subaccount_metadata,
)


def _get_working_source(
    template_type: ChartOfAccountsTemplateType,
):
    if (
        template_type
        == ChartOfAccountsTemplateType.GENERAL_291
    ):
        return GENERAL_291_WORKING_SUBACCOUNTS_V1

    if (
        template_type
        == ChartOfAccountsTemplateType.SIMPLIFIED_186
    ):
        return SIMPLIFIED_186_WORKING_SUBACCOUNTS_V1

    raise ValueError(
        (
            "Unsupported Ukrainian Chart of "
            f"Accounts template: {template_type!r}"
        )
    )


def build_ukrainian_working_system_account_definitions(
    template_type: ChartOfAccountsTemplateType,
) -> tuple[SystemAccountDefinition, ...]:
    synthetic_source = (
        get_ukrainian_synthetic_account_source(
            template_type
        )
    )

    working_source = _get_working_source(
        template_type
    )

    working_parent_codes = {
        item.parent_code
        for item in working_source
    }

    definitions: list[
        SystemAccountDefinition
    ] = []

    for source_account in synthetic_source:
        metadata = (
            get_ukrainian_synthetic_account_metadata(
                template_type=template_type,
                code=source_account.code,
            )
        )

        definitions.append(
            SystemAccountDefinition(
                code=source_account.code,
                name=source_account.name,
                account_type=metadata.account_type,
                normal_balance=(
                    metadata.normal_balance
                ),
                parent_code=None,
                is_postable=(
                    source_account.code
                    not in working_parent_codes
                ),
            )
        )

    for working_account in working_source:
        metadata = (
            get_ukrainian_working_subaccount_metadata(
                template_type=template_type,
                code=working_account.code,
            )
        )

        definitions.append(
            SystemAccountDefinition(
                code=working_account.code,
                name=working_account.name,
                account_type=metadata.account_type,
                normal_balance=(
                    metadata.normal_balance
                ),
                parent_code=(
                    working_account.parent_code
                ),
                is_postable=True,
            )
        )

    return tuple(definitions)


def build_ukrainian_working_system_account_catalog(
    template_type: ChartOfAccountsTemplateType,
) -> SystemAccountCatalog:
    return SystemAccountCatalog(
        build_ukrainian_working_system_account_definitions(
            template_type
        )
    )
