from datetime import date

from app.models.contract import Contract
from app.services.contract_types import (
    ContractStatus,
    ContractType,
)
from app.services.trade_document_types import (
    TradeDirection,
)


class TradeDocumentValidationError(Exception):
    """Base error for trade document validation."""


class TradeDocumentContractCompanyMismatchError(
    TradeDocumentValidationError
):
    pass


class TradeDocumentContractCounterpartyMismatchError(
    TradeDocumentValidationError
):
    pass


class TradeDocumentContractStatusError(
    TradeDocumentValidationError
):
    pass


class TradeDocumentContractTypeMismatchError(
    TradeDocumentValidationError
):
    pass


class TradeDocumentContractDateError(
    TradeDocumentValidationError
):
    pass


class TradeDocumentContractCurrencyMismatchError(
    TradeDocumentValidationError
):
    pass


def allowed_contract_types_for_direction(
    direction: TradeDirection,
) -> frozenset[ContractType]:
    if direction == TradeDirection.SALE:
        return frozenset(
            {
                ContractType.SALES,
                ContractType.MIXED,
            }
        )

    if direction == TradeDirection.PURCHASE:
        return frozenset(
            {
                ContractType.PURCHASE,
                ContractType.MIXED,
            }
        )

    raise TradeDocumentValidationError(
        f"Unsupported trade direction: {direction!r}"
    )


def validate_trade_document_contract(
    *,
    contract: Contract,
    company_id: int,
    counterparty_id: int,
    direction: TradeDirection,
    document_date: date,
    currency_code: str,
) -> None:
    if contract.company_id != company_id:
        raise TradeDocumentContractCompanyMismatchError(
            "Contract does not belong to this company"
        )

    if contract.counterparty_id != counterparty_id:
        raise TradeDocumentContractCounterpartyMismatchError(
            "Contract does not belong to this counterparty"
        )

    if contract.status != ContractStatus.ACTIVE:
        raise TradeDocumentContractStatusError(
            "Only active contracts can be used "
            "in trade documents"
        )

    allowed_types = (
        allowed_contract_types_for_direction(
            direction
        )
    )

    if contract.contract_type not in allowed_types:
        raise TradeDocumentContractTypeMismatchError(
            (
                f"Contract type "
                f"'{contract.contract_type.value}' "
                f"is not compatible with trade direction "
                f"'{direction.value}'"
            )
        )

    if document_date < contract.start_date:
        raise TradeDocumentContractDateError(
            "Document date is before contract start date"
        )

    if (
        contract.end_date is not None
        and document_date > contract.end_date
    ):
        raise TradeDocumentContractDateError(
            "Document date is after contract end date"
        )

    if contract.currency_code != currency_code:
        raise TradeDocumentContractCurrencyMismatchError(
            (
                "Trade document currency does not match "
                "contract currency: "
                f"contract={contract.currency_code}, "
                f"document={currency_code}"
            )
        )
