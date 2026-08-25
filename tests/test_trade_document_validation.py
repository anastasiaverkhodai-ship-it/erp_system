from datetime import date

import pytest

from app.models.contract import Contract
from app.services.contract_types import (
    ContractStatus,
    ContractType,
)
from app.services.trade_document_types import (
    TradeDirection,
)
from app.services.trade_document_validation import (
    TradeDocumentContractCompanyMismatchError,
    TradeDocumentContractCounterpartyMismatchError,
    TradeDocumentContractCurrencyMismatchError,
    TradeDocumentContractDateError,
    TradeDocumentContractStatusError,
    TradeDocumentContractTypeMismatchError,
    allowed_contract_types_for_direction,
    validate_trade_document_contract,
)


def _contract(
    *,
    company_id: int = 1,
    counterparty_id: int = 10,
    contract_type: ContractType = ContractType.SALES,
    status: ContractStatus = ContractStatus.ACTIVE,
    start_date: date = date(2026, 1, 1),
    end_date: date | None = date(2026, 12, 31),
    currency_code: str = "UAH",
) -> Contract:
    return Contract(
        company_id=company_id,
        counterparty_id=counterparty_id,
        number="C-001",
        contract_type=contract_type,
        status=status,
        start_date=start_date,
        end_date=end_date,
        currency_code=currency_code,
        payment_term_days=0,
    )


def _validate(
    contract: Contract,
    *,
    direction: TradeDirection = TradeDirection.SALE,
    company_id: int = 1,
    counterparty_id: int = 10,
    document_date: date = date(2026, 6, 1),
    currency_code: str = "UAH",
) -> None:
    validate_trade_document_contract(
        contract=contract,
        company_id=company_id,
        counterparty_id=counterparty_id,
        direction=direction,
        document_date=document_date,
        currency_code=currency_code,
    )


def test_sale_contract_types() -> None:
    assert allowed_contract_types_for_direction(
        TradeDirection.SALE
    ) == frozenset(
        {
            ContractType.SALES,
            ContractType.MIXED,
        }
    )


def test_purchase_contract_types() -> None:
    assert allowed_contract_types_for_direction(
        TradeDirection.PURCHASE
    ) == frozenset(
        {
            ContractType.PURCHASE,
            ContractType.MIXED,
        }
    )


def test_sales_contract_valid_for_sale() -> None:
    _validate(
        _contract(
            contract_type=ContractType.SALES
        )
    )


def test_mixed_contract_valid_for_sale() -> None:
    _validate(
        _contract(
            contract_type=ContractType.MIXED
        )
    )


def test_purchase_contract_valid_for_purchase() -> None:
    _validate(
        _contract(
            contract_type=ContractType.PURCHASE
        ),
        direction=TradeDirection.PURCHASE,
    )


def test_purchase_contract_rejected_for_sale() -> None:
    with pytest.raises(
        TradeDocumentContractTypeMismatchError
    ):
        _validate(
            _contract(
                contract_type=ContractType.PURCHASE
            )
        )


def test_sales_contract_rejected_for_purchase() -> None:
    with pytest.raises(
        TradeDocumentContractTypeMismatchError
    ):
        _validate(
            _contract(
                contract_type=ContractType.SALES
            ),
            direction=TradeDirection.PURCHASE,
        )


def test_non_active_contract_rejected() -> None:
    with pytest.raises(
        TradeDocumentContractStatusError
    ):
        _validate(
            _contract(
                status=ContractStatus.DRAFT
            )
        )


def test_wrong_company_rejected() -> None:
    with pytest.raises(
        TradeDocumentContractCompanyMismatchError
    ):
        _validate(
            _contract(
                company_id=2
            )
        )


def test_wrong_counterparty_rejected() -> None:
    with pytest.raises(
        TradeDocumentContractCounterpartyMismatchError
    ):
        _validate(
            _contract(
                counterparty_id=20
            )
        )


def test_document_before_contract_start_rejected() -> None:
    with pytest.raises(
        TradeDocumentContractDateError
    ):
        _validate(
            _contract(),
            document_date=date(2025, 12, 31),
        )


def test_document_after_contract_end_rejected() -> None:
    with pytest.raises(
        TradeDocumentContractDateError
    ):
        _validate(
            _contract(),
            document_date=date(2027, 1, 1),
        )


def test_contract_currency_mismatch_rejected() -> None:
    with pytest.raises(
        TradeDocumentContractCurrencyMismatchError
    ):
        _validate(
            _contract(
                currency_code="EUR"
            ),
            currency_code="UAH",
        )
