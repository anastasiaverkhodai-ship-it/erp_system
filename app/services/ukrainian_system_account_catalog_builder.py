from app.services.chart_of_accounts_template_types import (
    ChartOfAccountsTemplateType,
)
from app.services.system_account_catalog import (
    SystemAccountCatalog,
)
from app.services.system_account_definition import (
    SystemAccountDefinition,
)
from app.services.ukrainian_chart_source_registry import (
    get_ukrainian_synthetic_account_source,
)
from app.services.ukrainian_chart_synthetic_metadata import (
    get_ukrainian_synthetic_account_metadata,
)


def build_ukrainian_system_account_definitions(
    template_type: ChartOfAccountsTemplateType,
) -> tuple[
    SystemAccountDefinition,
    ...,
]:
    source_accounts = (
        get_ukrainian_synthetic_account_source(
            template_type
        )
    )

    definitions: list[
        SystemAccountDefinition
    ] = []

    for source_account in source_accounts:
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
                normal_balance=metadata.normal_balance,
                parent_code=None,
                is_postable=True,
            )
        )

    return tuple(definitions)


def build_ukrainian_system_account_catalog(
    template_type: ChartOfAccountsTemplateType,
) -> SystemAccountCatalog:
    definitions = (
        build_ukrainian_system_account_definitions(
            template_type
        )
    )

    return SystemAccountCatalog(definitions)
