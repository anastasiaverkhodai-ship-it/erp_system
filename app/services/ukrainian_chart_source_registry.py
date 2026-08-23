from collections.abc import Mapping
from types import MappingProxyType

from app.services.chart_of_accounts_template_types import (
    ChartOfAccountsTemplateType,
)
from app.services.ukrainian_chart_general_291_source import (
    GENERAL_291_SYNTHETIC_ACCOUNTS,
)
from app.services.ukrainian_chart_simplified_186_source import (
    SIMPLIFIED_186_SYNTHETIC_ACCOUNTS,
)
from app.services.ukrainian_chart_source_definition import (
    UkrainianSyntheticAccountSource,
)


class UkrainianChartSourceRegistryError(Exception):
    pass


class UnsupportedUkrainianChartTemplateError(
    UkrainianChartSourceRegistryError
):
    pass


_UKRAINIAN_CHART_SOURCES: Mapping[
    ChartOfAccountsTemplateType,
    tuple[
        UkrainianSyntheticAccountSource,
        ...,
    ],
] = MappingProxyType(
    {
        ChartOfAccountsTemplateType.GENERAL_291: (
            GENERAL_291_SYNTHETIC_ACCOUNTS
        ),
        ChartOfAccountsTemplateType.SIMPLIFIED_186: (
            SIMPLIFIED_186_SYNTHETIC_ACCOUNTS
        ),
    }
)


def get_ukrainian_synthetic_account_source(
    template_type: ChartOfAccountsTemplateType,
) -> tuple[
    UkrainianSyntheticAccountSource,
    ...,
]:
    try:
        return _UKRAINIAN_CHART_SOURCES[template_type]
    except KeyError as exc:
        raise UnsupportedUkrainianChartTemplateError(
            "Unsupported Ukrainian Chart of Accounts "
            f"template: {template_type}"
        ) from exc


def validate_ukrainian_chart_source_registry() -> None:
    expected_templates = set(
        ChartOfAccountsTemplateType
    )
    registered_templates = set(
        _UKRAINIAN_CHART_SOURCES
    )

    if registered_templates != expected_templates:
        missing = (
            expected_templates
            - registered_templates
        )
        unexpected = (
            registered_templates
            - expected_templates
        )

        raise UkrainianChartSourceRegistryError(
            "Ukrainian Chart of Accounts source "
            "registry mismatch. "
            f"Missing: {sorted(item.value for item in missing)}. "
            "Unexpected: "
            f"{sorted(item.value for item in unexpected)}."
        )

    for template_type, accounts in (
        _UKRAINIAN_CHART_SOURCES.items()
    ):
        if not accounts:
            raise UkrainianChartSourceRegistryError(
                "Ukrainian Chart of Accounts source "
                "cannot be empty: "
                f"{template_type.value}"
            )


validate_ukrainian_chart_source_registry()
