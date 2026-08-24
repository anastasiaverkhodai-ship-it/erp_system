from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from app.services.account_types import (
    AccountNormalBalance,
    AccountType,
)
from app.services.chart_of_accounts_template_types import (
    ChartOfAccountsTemplateType,
)
from app.services.ukrainian_chart_general_291_working_subaccounts_source import (
    GENERAL_291_WORKING_SUBACCOUNTS_V1,
)
from app.services.ukrainian_chart_simplified_186_working_subaccounts_source import (
    SIMPLIFIED_186_WORKING_SUBACCOUNTS_V1,
)


@dataclass(
    frozen=True,
    slots=True,
)
class UkrainianWorkingSubaccountMetadata:
    account_type: AccountType
    normal_balance: AccountNormalBalance


GENERAL_291_WORKING_SUBACCOUNT_METADATA: Mapping[
    str,
    UkrainianWorkingSubaccountMetadata,
] = MappingProxyType(
    {
        "281": UkrainianWorkingSubaccountMetadata(
            account_type=AccountType.ASSET,
            normal_balance=AccountNormalBalance.DEBIT,
        ),
        "311": UkrainianWorkingSubaccountMetadata(
            account_type=AccountType.ASSET,
            normal_balance=AccountNormalBalance.DEBIT,
        ),
        "361": UkrainianWorkingSubaccountMetadata(
            account_type=AccountType.ASSET,
            normal_balance=AccountNormalBalance.DEBIT,
        ),
        "371": UkrainianWorkingSubaccountMetadata(
            account_type=AccountType.ASSET,
            normal_balance=AccountNormalBalance.DEBIT,
        ),
        "631": UkrainianWorkingSubaccountMetadata(
            account_type=AccountType.LIABILITY,
            normal_balance=AccountNormalBalance.CREDIT,
        ),
        "641": UkrainianWorkingSubaccountMetadata(
            account_type=AccountType.LIABILITY,
            normal_balance=AccountNormalBalance.DEBIT_CREDIT,
        ),
        "643": UkrainianWorkingSubaccountMetadata(
            account_type=AccountType.LIABILITY,
            normal_balance=AccountNormalBalance.DEBIT_CREDIT,
        ),
        "644": UkrainianWorkingSubaccountMetadata(
            account_type=AccountType.LIABILITY,
            normal_balance=AccountNormalBalance.DEBIT_CREDIT,
        ),
        "681": UkrainianWorkingSubaccountMetadata(
            account_type=AccountType.LIABILITY,
            normal_balance=AccountNormalBalance.CREDIT,
        ),
        "702": UkrainianWorkingSubaccountMetadata(
            account_type=AccountType.INCOME,
            normal_balance=AccountNormalBalance.CREDIT,
        ),
        "704": UkrainianWorkingSubaccountMetadata(
            account_type=AccountType.INCOME,
            normal_balance=AccountNormalBalance.DEBIT,
        ),
        "902": UkrainianWorkingSubaccountMetadata(
            account_type=AccountType.EXPENSE,
            normal_balance=AccountNormalBalance.DEBIT,
        ),
    }
)


SIMPLIFIED_186_WORKING_SUBACCOUNT_METADATA: Mapping[
    str,
    UkrainianWorkingSubaccountMetadata,
] = MappingProxyType({})


WORKING_SUBACCOUNT_METADATA_BY_TEMPLATE = (
    MappingProxyType(
        {
            ChartOfAccountsTemplateType.GENERAL_291:
                GENERAL_291_WORKING_SUBACCOUNT_METADATA,

            ChartOfAccountsTemplateType.SIMPLIFIED_186:
                SIMPLIFIED_186_WORKING_SUBACCOUNT_METADATA,
        }
    )
)


def get_ukrainian_working_subaccount_metadata(
    *,
    template_type: ChartOfAccountsTemplateType,
    code: str,
) -> UkrainianWorkingSubaccountMetadata:
    metadata = (
        WORKING_SUBACCOUNT_METADATA_BY_TEMPLATE[
            template_type
        ]
    )

    try:
        return metadata[code]
    except KeyError as exc:
        raise KeyError(
            (
                "Working subaccount metadata "
                f"not found: template="
                f"{template_type.value!r}, "
                f"code={code!r}"
            )
        ) from exc


def validate_ukrainian_working_subaccount_metadata(
) -> None:
    source_by_template = {
        ChartOfAccountsTemplateType.GENERAL_291:
            GENERAL_291_WORKING_SUBACCOUNTS_V1,

        ChartOfAccountsTemplateType.SIMPLIFIED_186:
            SIMPLIFIED_186_WORKING_SUBACCOUNTS_V1,
    }

    for template_type, source in (
        source_by_template.items()
    ):
        source_codes = {
            item.code
            for item in source
        }

        metadata_codes = set(
            WORKING_SUBACCOUNT_METADATA_BY_TEMPLATE[
                template_type
            ]
        )

        if source_codes != metadata_codes:
            raise ValueError(
                (
                    "Working subaccount metadata "
                    "coverage mismatch for "
                    f"{template_type.value}: "
                    f"source={sorted(source_codes)}, "
                    f"metadata="
                    f"{sorted(metadata_codes)}"
                )
            )


validate_ukrainian_working_subaccount_metadata()
