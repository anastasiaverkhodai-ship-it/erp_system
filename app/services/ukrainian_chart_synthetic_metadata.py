from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from app.services.account_types import (
    AccountNormalBalance,
    AccountType,
)
from app.services.chart_of_accounts_template_types import (
    ChartOfAccountsTemplateType,
)
from app.services.ukrainian_chart_source_registry import (
    get_ukrainian_synthetic_account_source,
)


@dataclass(
    frozen=True,
    slots=True,
)
class UkrainianSyntheticAccountMetadata:
    account_type: AccountType
    normal_balance: AccountNormalBalance


class UkrainianSyntheticAccountMetadataError(Exception):
    pass


class UkrainianSyntheticAccountMetadataNotFoundError(
    UkrainianSyntheticAccountMetadataError
):
    pass


def _assign(
    target: dict[
        str,
        UkrainianSyntheticAccountMetadata,
    ],
    *,
    codes: tuple[str, ...],
    account_type: AccountType,
    normal_balance: AccountNormalBalance,
) -> None:
    metadata = UkrainianSyntheticAccountMetadata(
        account_type=account_type,
        normal_balance=normal_balance,
    )

    for code in codes:
        if code in target:
            raise UkrainianSyntheticAccountMetadataError(
                "Duplicate synthetic account metadata: "
                f"{code}"
            )

        target[code] = metadata


def _build_general_291_metadata() -> Mapping[
    str,
    UkrainianSyntheticAccountMetadata,
]:
    metadata: dict[
        str,
        UkrainianSyntheticAccountMetadata,
    ] = {}

    # Assets with normal debit balance.
    _assign(
        metadata,
        codes=(
            "10", "11", "12", "14", "15",
            "16", "17", "18", "19",
            "20", "21", "22", "23", "24",
            "25", "26", "27", "28",
            "30", "31", "33", "34", "35",
            "36", "37", "39",
        ),
        account_type=AccountType.ASSET,
        normal_balance=AccountNormalBalance.DEBIT,
    )

    # Contra-asset accounts.
    _assign(
        metadata,
        codes=(
            "13",
            "38",
        ),
        account_type=AccountType.ASSET,
        normal_balance=AccountNormalBalance.CREDIT,
    )

    # Equity accounts with normal credit balance.
    _assign(
        metadata,
        codes=(
            "40",
            "41",
            "42",
            "43",
        ),
        account_type=AccountType.EQUITY,
        normal_balance=AccountNormalBalance.CREDIT,
    )

    # Retained earnings / uncovered losses.
    _assign(
        metadata,
        codes=("44",),
        account_type=AccountType.EQUITY,
        normal_balance=AccountNormalBalance.DEBIT_CREDIT,
    )

    # Contra-equity accounts.
    _assign(
        metadata,
        codes=(
            "45",
            "46",
        ),
        account_type=AccountType.EQUITY,
        normal_balance=AccountNormalBalance.DEBIT,
    )

    # Liabilities with normal credit balance.
    _assign(
        metadata,
        codes=(
            "47", "48", "49",
            "50", "51", "52", "53", "54", "55",
            "60", "61", "62", "63",
            "65", "66", "69",
        ),
        account_type=AccountType.LIABILITY,
        normal_balance=AccountNormalBalance.CREDIT,
    )

    # Settlement accounts that may carry either balance.
    _assign(
        metadata,
        codes=(
            "64",
            "67",
            "68",
        ),
        account_type=AccountType.LIABILITY,
        normal_balance=AccountNormalBalance.DEBIT_CREDIT,
    )

    # Revenue accounts.
    _assign(
        metadata,
        codes=(
            "70",
            "71",
            "72",
            "73",
            "74",
            "76",
        ),
        account_type=AccountType.INCOME,
        normal_balance=AccountNormalBalance.CREDIT,
    )

    # Closing financial-result account.
    _assign(
        metadata,
        codes=("79",),
        account_type=AccountType.EQUITY,
        normal_balance=AccountNormalBalance.DEBIT_CREDIT,
    )

    # Expense accounts.
    _assign(
        metadata,
        codes=(
            "80", "81", "82", "83", "84",
            "90", "91", "92", "93", "94",
            "95", "96", "97", "98",
        ),
        account_type=AccountType.EXPENSE,
        normal_balance=AccountNormalBalance.DEBIT,
    )

    # Off-balance accounts can represent both
    # debit-side and credit-side memorandum balances.
    _assign(
        metadata,
        codes=(
            "01", "02", "03", "04", "05",
            "06", "07", "08", "09",
        ),
        account_type=AccountType.OFF_BALANCE,
        normal_balance=AccountNormalBalance.DEBIT_CREDIT,
    )

    return MappingProxyType(metadata)


def _build_simplified_186_metadata() -> Mapping[
    str,
    UkrainianSyntheticAccountMetadata,
]:
    metadata: dict[
        str,
        UkrainianSyntheticAccountMetadata,
    ] = {}

    _assign(
        metadata,
        codes=(
            "10", "14", "15", "16", "18",
            "20", "21", "23", "26",
            "30", "31", "35", "37", "39",
        ),
        account_type=AccountType.ASSET,
        normal_balance=AccountNormalBalance.DEBIT,
    )

    _assign(
        metadata,
        codes=("13",),
        account_type=AccountType.ASSET,
        normal_balance=AccountNormalBalance.CREDIT,
    )

    # Simplified account 40 aggregates own capital,
    # including changes that may move either way.
    _assign(
        metadata,
        codes=(
            "40",
            "44",
        ),
        account_type=AccountType.EQUITY,
        normal_balance=AccountNormalBalance.DEBIT_CREDIT,
    )

    _assign(
        metadata,
        codes=(
            "47",
            "48",
            "55",
            "66",
            "69",
        ),
        account_type=AccountType.LIABILITY,
        normal_balance=AccountNormalBalance.CREDIT,
    )

    _assign(
        metadata,
        codes=(
            "64",
            "68",
        ),
        account_type=AccountType.LIABILITY,
        normal_balance=AccountNormalBalance.DEBIT_CREDIT,
    )

    _assign(
        metadata,
        codes=(
            "70",
            "74",
        ),
        account_type=AccountType.INCOME,
        normal_balance=AccountNormalBalance.CREDIT,
    )

    _assign(
        metadata,
        codes=("79",),
        account_type=AccountType.EQUITY,
        normal_balance=AccountNormalBalance.DEBIT_CREDIT,
    )

    _assign(
        metadata,
        codes=(
            "90",
            "91",
            "97",
        ),
        account_type=AccountType.EXPENSE,
        normal_balance=AccountNormalBalance.DEBIT,
    )

    return MappingProxyType(metadata)


_SYNTHETIC_METADATA_BY_TEMPLATE: Mapping[
    ChartOfAccountsTemplateType,
    Mapping[
        str,
        UkrainianSyntheticAccountMetadata,
    ],
] = MappingProxyType(
    {
        ChartOfAccountsTemplateType.GENERAL_291: (
            _build_general_291_metadata()
        ),
        ChartOfAccountsTemplateType.SIMPLIFIED_186: (
            _build_simplified_186_metadata()
        ),
    }
)


def get_ukrainian_synthetic_account_metadata(
    *,
    template_type: ChartOfAccountsTemplateType,
    code: str,
) -> UkrainianSyntheticAccountMetadata:
    try:
        template_metadata = (
            _SYNTHETIC_METADATA_BY_TEMPLATE[
                template_type
            ]
        )
        return template_metadata[code]
    except KeyError as exc:
        raise (
            UkrainianSyntheticAccountMetadataNotFoundError(
                "Synthetic account metadata not found "
                f"for template={template_type.value}, "
                f"code={code}"
            )
        ) from exc


def validate_ukrainian_synthetic_account_metadata() -> None:
    for template_type in ChartOfAccountsTemplateType:
        source = get_ukrainian_synthetic_account_source(
            template_type
        )
        source_codes = {
            account.code
            for account in source
        }

        metadata_codes = set(
            _SYNTHETIC_METADATA_BY_TEMPLATE[
                template_type
            ]
        )

        if source_codes != metadata_codes:
            missing = source_codes - metadata_codes
            unexpected = metadata_codes - source_codes

            raise UkrainianSyntheticAccountMetadataError(
                "Synthetic account metadata mismatch "
                f"for {template_type.value}. "
                f"Missing: {sorted(missing)}. "
                f"Unexpected: {sorted(unexpected)}."
            )


validate_ukrainian_synthetic_account_metadata()
