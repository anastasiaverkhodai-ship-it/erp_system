import asyncio
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import (
    AsyncMock,
    Mock,
)

import pytest

import app.services.payment_settlement_service as service

from app.services.counterparty_open_item_types import (
    CounterpartyOpenItemStatus,
    CounterpartyOpenItemType,
)
from app.services.payment_settlement_service import (
    DuplicateActivePaymentSettlementError,
    OpenItemOverAllocationError,
    PaymentOverAllocationError,
    PaymentSettlementContractError,
    PaymentSettlementCounterpartyError,
    PaymentSettlementCurrencyError,
    PaymentSettlementDirectionError,
    PaymentSettlementOpenItemStatusError,
    PaymentSettlementPaymentStatusError,
    calculate_open_item_status,
    create_payment_settlement_allocation,
    create_payment_settlement_plan,
    get_expected_open_item_type,
    reverse_payment_settlement_allocation,
    validate_payment_settlement_match,
)
from app.services.payment_types import (
    PaymentDirection,
    PaymentSettlementAllocationStatus,
    PaymentStatus,
)


def payment(
    *,
    direction=PaymentDirection.INCOMING,
    status=PaymentStatus.CONFIRMED,
    contract_id=None,
    amount=Decimal("100.00"),
):
    return SimpleNamespace(
        id=10,
        company_id=1,
        counterparty_id=20,
        contract_id=contract_id,
        direction=direction,
        status=status,
        currency_code="UAH",
        amount=amount,
    )


def open_item(
    *,
    item_type=(
        CounterpartyOpenItemType.RECEIVABLE
    ),
    status=(
        CounterpartyOpenItemStatus.OPEN
    ),
    contract_id=None,
    amount=Decimal("100.00"),
):
    return SimpleNamespace(
        id=30,
        company_id=1,
        counterparty_id=20,
        contract_id=contract_id,
        item_type=item_type,
        status=status,
        currency_code="UAH",
        original_amount=amount,
    )


def test_direction_mapping():
    assert (
        get_expected_open_item_type(
            PaymentDirection.INCOMING
        )
        == CounterpartyOpenItemType.RECEIVABLE
    )

    assert (
        get_expected_open_item_type(
            PaymentDirection.OUTGOING
        )
        == CounterpartyOpenItemType.PAYABLE
    )


def test_valid_incoming_receivable():
    validate_payment_settlement_match(
        payment=payment(),
        open_item=open_item(),
    )


def test_valid_outgoing_payable():
    validate_payment_settlement_match(
        payment=payment(
            direction=(
                PaymentDirection.OUTGOING
            )
        ),
        open_item=open_item(
            item_type=(
                CounterpartyOpenItemType.PAYABLE
            )
        ),
    )


def test_payment_must_be_confirmed():
    with pytest.raises(
        PaymentSettlementPaymentStatusError
    ):
        validate_payment_settlement_match(
            payment=payment(
                status=PaymentStatus.DRAFT
            ),
            open_item=open_item(),
        )


def test_cancelled_open_item_rejected():
    with pytest.raises(
        PaymentSettlementOpenItemStatusError
    ):
        validate_payment_settlement_match(
            payment=payment(),
            open_item=open_item(
                status=(
                    CounterpartyOpenItemStatus.CANCELLED
                )
            ),
        )


def test_direction_mismatch_rejected():
    with pytest.raises(
        PaymentSettlementDirectionError
    ):
        validate_payment_settlement_match(
            payment=payment(),
            open_item=open_item(
                item_type=(
                    CounterpartyOpenItemType.PAYABLE
                )
            ),
        )


def test_counterparty_mismatch_rejected():
    item = open_item()
    item.counterparty_id = 999

    with pytest.raises(
        PaymentSettlementCounterpartyError
    ):
        validate_payment_settlement_match(
            payment=payment(),
            open_item=item,
        )


def test_payment_contract_must_match():
    with pytest.raises(
        PaymentSettlementContractError
    ):
        validate_payment_settlement_match(
            payment=payment(
                contract_id=50
            ),
            open_item=open_item(
                contract_id=60
            ),
        )


def test_payment_without_contract_is_flexible():
    validate_payment_settlement_match(
        payment=payment(
            contract_id=None
        ),
        open_item=open_item(
            contract_id=60
        ),
    )


def test_currency_mismatch_rejected():
    item = open_item()
    item.currency_code = "EUR"

    with pytest.raises(
        PaymentSettlementCurrencyError
    ):
        validate_payment_settlement_match(
            payment=payment(),
            open_item=item,
        )


@pytest.mark.parametrize(
    (
        "settled",
        "expected",
    ),
    [
        (
            Decimal("0"),
            CounterpartyOpenItemStatus.OPEN,
        ),
        (
            Decimal("40"),
            CounterpartyOpenItemStatus.PARTIALLY_SETTLED,
        ),
        (
            Decimal("100"),
            CounterpartyOpenItemStatus.SETTLED,
        ),
    ],
)
def test_open_item_status_calculation(
    settled,
    expected,
):
    assert (
        calculate_open_item_status(
            original_amount=Decimal("100"),
            settled_amount=settled,
        )
        == expected
    )


def test_partial_settlement_plan():
    plan = (
        create_payment_settlement_plan(
            payment=payment(
                amount=Decimal("100")
            ),
            open_item=open_item(
                amount=Decimal("200")
            ),
            amount=Decimal("40"),
            payment_settled_before=(
                Decimal("0")
            ),
            open_item_settled_before=(
                Decimal("0")
            ),
        )
    )

    assert (
        plan.payment_settled_after
        == Decimal("40")
    )

    assert (
        plan.open_item_settled_after
        == Decimal("40")
    )

    assert (
        plan.open_item_status_after
        == CounterpartyOpenItemStatus.PARTIALLY_SETTLED
    )


def test_exact_open_item_settlement_plan():
    plan = (
        create_payment_settlement_plan(
            payment=payment(
                amount=Decimal("100")
            ),
            open_item=open_item(
                amount=Decimal("100")
            ),
            amount=Decimal("100"),
            payment_settled_before=(
                Decimal("0")
            ),
            open_item_settled_before=(
                Decimal("0")
            ),
        )
    )

    assert (
        plan.open_item_status_after
        == CounterpartyOpenItemStatus.SETTLED
    )


def test_payment_overallocation_rejected():
    with pytest.raises(
        PaymentOverAllocationError
    ):
        create_payment_settlement_plan(
            payment=payment(
                amount=Decimal("100")
            ),
            open_item=open_item(
                amount=Decimal("200")
            ),
            amount=Decimal("20"),
            payment_settled_before=(
                Decimal("90")
            ),
            open_item_settled_before=(
                Decimal("0")
            ),
        )


def test_open_item_overallocation_rejected():
    with pytest.raises(
        OpenItemOverAllocationError
    ):
        create_payment_settlement_plan(
            payment=payment(
                amount=Decimal("200")
            ),
            open_item=open_item(
                amount=Decimal("100")
            ),
            amount=Decimal("20"),
            payment_settled_before=(
                Decimal("0")
            ),
            open_item_settled_before=(
                Decimal("90")
            ),
        )


def test_create_allocation_partial(
    monkeypatch,
):
    db = SimpleNamespace(
        add=Mock(),
        flush=AsyncMock(),
    )

    pay = payment(
        amount=Decimal("100")
    )

    item = open_item(
        amount=Decimal("100")
    )
    item.trade_document_id = 40

    monkeypatch.setattr(
        service,
        "get_locked_settlement_payment",
        AsyncMock(
            return_value=pay
        ),
    )

    monkeypatch.setattr(
        service,
        "get_locked_settlement_open_item",
        AsyncMock(
            return_value=item
        ),
    )

    monkeypatch.setattr(
        service,
        "_active_settlement_pair_exists",
        AsyncMock(
            return_value=False
        ),
    )

    monkeypatch.setattr(
        service,
        "get_active_payment_settled_amount",
        AsyncMock(
            return_value=Decimal("0")
        ),
    )

    monkeypatch.setattr(
        service,
        "get_active_open_item_settled_amount",
        AsyncMock(
            return_value=Decimal("0")
        ),
    )

    result = asyncio.run(
        create_payment_settlement_allocation(
            db,
            company_id=1,
            payment_id=10,
            open_item_id=30,
            amount=Decimal("40"),
            created_by=99,
        )
    )

    assert result.payment_id == 10
    assert result.open_item_id == 30

    assert (
        result.amount
        == Decimal("40.00")
    )

    assert (
        result.status
        == PaymentSettlementAllocationStatus.ACTIVE
    )

    assert (
        item.status
        == CounterpartyOpenItemStatus.PARTIALLY_SETTLED
    )

    db.add.assert_called_once_with(
        result
    )

    db.flush.assert_awaited_once_with()


def test_duplicate_active_pair_rejected(
    monkeypatch,
):
    db = SimpleNamespace(
        add=Mock(),
        flush=AsyncMock(),
    )

    monkeypatch.setattr(
        service,
        "get_locked_settlement_payment",
        AsyncMock(
            return_value=payment()
        ),
    )

    monkeypatch.setattr(
        service,
        "get_locked_settlement_open_item",
        AsyncMock(
            return_value=open_item()
        ),
    )

    monkeypatch.setattr(
        service,
        "_active_settlement_pair_exists",
        AsyncMock(
            return_value=True
        ),
    )

    with pytest.raises(
        DuplicateActivePaymentSettlementError
    ):
        asyncio.run(
            create_payment_settlement_allocation(
                db,
                company_id=1,
                payment_id=10,
                open_item_id=30,
                amount=Decimal("10"),
                created_by=99,
            )
        )

    db.add.assert_not_called()
    db.flush.assert_not_awaited()


def test_reverse_allocation_restores_open_status(
    monkeypatch,
):
    db = SimpleNamespace(
        flush=AsyncMock(),
        execute=AsyncMock(),
    )

    pay = payment()

    item = open_item(
        status=(
            CounterpartyOpenItemStatus.PARTIALLY_SETTLED
        )
    )
    item.trade_document_id = 40

    allocation = SimpleNamespace(
        id=50,
        company_id=1,
        payment_id=10,
        open_item_id=30,
        amount=Decimal("40"),
        status=(
            PaymentSettlementAllocationStatus.ACTIVE
        ),
        reversed_by=None,
        reversed_at=None,
    )

    monkeypatch.setattr(
        service,
        "_get_settlement_allocation_identity",
        AsyncMock(
            return_value=(
                10,
                30,
            )
        ),
    )

    monkeypatch.setattr(
        service,
        "get_locked_settlement_payment",
        AsyncMock(
            return_value=pay
        ),
    )

    monkeypatch.setattr(
        service,
        "get_locked_settlement_open_item",
        AsyncMock(
            return_value=item
        ),
    )

    db.execute.return_value = (
        SimpleNamespace(
            scalar_one_or_none=lambda: (
                allocation
            )
        )
    )

    monkeypatch.setattr(
        service,
        "get_active_payment_settled_amount",
        AsyncMock(
            return_value=Decimal("40")
        ),
    )

    monkeypatch.setattr(
        service,
        "get_active_open_item_settled_amount",
        AsyncMock(
            return_value=Decimal("40")
        ),
    )

    result = asyncio.run(
        reverse_payment_settlement_allocation(
            db,
            company_id=1,
            allocation_id=50,
            reversed_by=99,
        )
    )

    assert result is allocation

    assert (
        allocation.status
        == PaymentSettlementAllocationStatus.REVERSED
    )

    assert allocation.reversed_by == 99

    assert (
        allocation.reversed_at
        is not None
    )

    assert (
        item.status
        == CounterpartyOpenItemStatus.OPEN
    )

    db.flush.assert_awaited_once_with()


def test_payment_unallocated_amount():
    assert (
        service.calculate_payment_unallocated_amount(
            payment_amount=Decimal("100"),
            settled_amount=Decimal("40"),
        )
        == Decimal("60")
    )


def test_open_item_open_amount():
    assert (
        service.calculate_open_item_open_amount(
            original_amount=Decimal("100"),
            settled_amount=Decimal("40"),
            status=(
                CounterpartyOpenItemStatus.PARTIALLY_SETTLED
            ),
        )
        == Decimal("60")
    )


def test_cancelled_open_item_open_amount_is_zero():
    assert (
        service.calculate_open_item_open_amount(
            original_amount=Decimal("100"),
            settled_amount=Decimal("0"),
            status=(
                CounterpartyOpenItemStatus.CANCELLED
            ),
        )
        == Decimal("0")
    )


def test_open_item_status_drift_is_rejected():
    with pytest.raises(
        service.PaymentSettlementDataIntegrityError
    ):
        service.calculate_open_item_open_amount(
            original_amount=Decimal("100"),
            settled_amount=Decimal("40"),
            status=(
                CounterpartyOpenItemStatus.OPEN
            ),
        )


@pytest.fixture(autouse=True)
def _default_settlement_creation_accounting(
    monkeypatch,
):
    monkeypatch.setattr(
        service,
        "generate_and_post_settlement_journal_entry",
        AsyncMock(),
    )


@pytest.fixture(autouse=True)
def _default_settlement_reversal_accounting(
    monkeypatch,
):
    monkeypatch.setattr(
        service,
        "reverse_settlement_journal_entry",
        AsyncMock(),
    )


# STEP17B_AUTOUSE_SETTLEMENT_VAT_STUB
import pytest as _step17b_pytest
from unittest.mock import AsyncMock as _Step17BAsyncMock
import app.services.payment_settlement_service as _step17b_settlement_service


@_step17b_pytest.fixture(autouse=True)
def _step17b_stub_settlement_vat_recognition(
    monkeypatch,
):
    stub = _Step17BAsyncMock(
        return_value=()
    )

    monkeypatch.setattr(
        _step17b_settlement_service,
        "reconcile_tax_for_invoice",
        stub,
    )

    return stub


@pytest.fixture(autouse=True)
def _default_customer_advance_clearing_lifecycle_for_legacy_payment_unit_tests(
    monkeypatch,
):
    """
    Pre-existing PaymentSettlement service tests exercise
    commercial allocation state, validation and status changes.

    CustomerAdvanceClearing is now a downstream integration
    lifecycle owned by dedicated customer-clearing tests.

    Keep these legacy unit tests isolated from real SQL loading.
    """
    from types import SimpleNamespace as _SimpleNamespace
    from unittest.mock import AsyncMock as _AsyncMock

    import app.services.payment_settlement_service as _payment_service

    hook = _AsyncMock(
        return_value=_SimpleNamespace(
            created_events=(),
        )
    )

    monkeypatch.setattr(
        _payment_service,
        (
            "reconcile_customer_advance_"
            "clearing_lifecycle_for_invoice"
        ),
        hook,
    )

    return hook
