import asyncio
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.services.payment_lifecycle_service as service

from app.models.payment import Payment
from app.services.contract_types import ContractStatus
from app.services.payment_lifecycle_service import (
    PaymentActorError,
    PaymentAmountError,
    PaymentCompanyInvalidError,
    PaymentContractInvalidError,
    PaymentCounterpartyInvalidError,
    PaymentCurrencyError,
    PaymentDirectionError,
    PaymentNumberError,
    PaymentStatusError,
    cancel_payment,
    confirm_payment,
    create_payment_draft,
    normalize_payment_amount,
    normalize_payment_currency_code,
    normalize_payment_direction,
    normalize_payment_number,
    revalidate_payment_references,
    validate_payment_cancellation,
    validate_payment_confirmation,
)
from app.services.payment_types import (
    PaymentDirection,
    PaymentStatus,
)


class FakeResult:
    def __init__(
        self,
        value=None,
    ):
        self.value = value

    def scalar_one_or_none(
        self,
    ):
        return self.value


class FakeDB:
    def __init__(
        self,
        results=(),
    ):
        self.results = list(
            results
        )

        self.added = []
        self.flush = AsyncMock()

    async def execute(
        self,
        _statement,
    ):
        if not self.results:
            raise AssertionError(
                "Unexpected database query"
            )

        return self.results.pop(
            0
        )

    def add(
        self,
        value,
    ):
        self.added.append(
            value
        )


def payment(
    *,
    status=PaymentStatus.DRAFT,
    contract_id=None,
    currency_code="UAH",
    amount=Decimal("100.00"),
):
    return Payment(
        id=10,
        company_id=1,
        counterparty_id=2,
        contract_id=contract_id,
        number="PAY-001",
        direction=(
            PaymentDirection.INCOMING
        ),
        status=status,
        payment_date=date(
            2026,
            8,
            29,
        ),
        currency_code=(
            currency_code
        ),
        amount=amount,
        created_by=3,
    )


def test_normalizers():
    assert (
        normalize_payment_currency_code(
            " uah "
        )
        == "UAH"
    )

    assert (
        normalize_payment_number(
            " PAY-1 "
        )
        == "PAY-1"
    )

    assert (
        normalize_payment_amount(
            Decimal("12.34")
        )
        == Decimal("12.34")
    )

    assert (
        normalize_payment_direction(
            PaymentDirection.OUTGOING
        )
        == PaymentDirection.OUTGOING
    )


@pytest.mark.parametrize(
    (
        "call",
        "error",
    ),
    [
        (
            lambda: (
                normalize_payment_currency_code(
                    "UA"
                )
            ),
            PaymentCurrencyError,
        ),
        (
            lambda: (
                normalize_payment_number(
                    "   "
                )
            ),
            PaymentNumberError,
        ),
        (
            lambda: (
                normalize_payment_amount(
                    Decimal("0")
                )
            ),
            PaymentAmountError,
        ),
        (
            lambda: (
                normalize_payment_direction(
                    "invalid"
                )
            ),
            PaymentDirectionError,
        ),
    ],
)
def test_invalid_normalized_values(
    call,
    error,
):
    with pytest.raises(
        error
    ):
        call()


def test_draft_confirmation_valid():
    validate_payment_confirmation(
        payment()
    )


def test_only_draft_can_be_confirmed():
    with pytest.raises(
        PaymentStatusError
    ):
        validate_payment_confirmation(
            payment(
                status=(
                    PaymentStatus.CONFIRMED
                )
            )
        )


@pytest.mark.parametrize(
    "status",
    [
        PaymentStatus.DRAFT,
        PaymentStatus.CONFIRMED,
    ],
)
def test_draft_and_confirmed_can_cancel(
    status,
):
    validate_payment_cancellation(
        payment(
            status=status
        )
    )


def test_cancelled_cannot_cancel_again():
    with pytest.raises(
        PaymentStatusError
    ):
        validate_payment_cancellation(
            payment(
                status=(
                    PaymentStatus.CANCELLED
                )
            )
        )


def test_revalidation_without_contract():
    db = FakeDB(
        [
            FakeResult(1),
            FakeResult(2),
        ]
    )

    asyncio.run(
        revalidate_payment_references(
            db,
            payment=payment(),
        )
    )


def test_inactive_company_rejected():
    db = FakeDB(
        [
            FakeResult(None),
        ]
    )

    with pytest.raises(
        PaymentCompanyInvalidError
    ):
        asyncio.run(
            revalidate_payment_references(
                db,
                payment=payment(),
            )
        )


def test_inactive_counterparty_rejected():
    db = FakeDB(
        [
            FakeResult(1),
            FakeResult(None),
        ]
    )

    with pytest.raises(
        PaymentCounterpartyInvalidError
    ):
        asyncio.run(
            revalidate_payment_references(
                db,
                payment=payment(),
            )
        )


def test_valid_active_contract():
    contract = SimpleNamespace(
        status=ContractStatus.ACTIVE,
        currency_code="UAH",
    )

    db = FakeDB(
        [
            FakeResult(1),
            FakeResult(2),
            FakeResult(contract),
        ]
    )

    asyncio.run(
        revalidate_payment_references(
            db,
            payment=payment(
                contract_id=50
            ),
        )
    )


def test_invalid_contract_rejected():
    db = FakeDB(
        [
            FakeResult(1),
            FakeResult(2),
            FakeResult(None),
        ]
    )

    with pytest.raises(
        PaymentContractInvalidError
    ):
        asyncio.run(
            revalidate_payment_references(
                db,
                payment=payment(
                    contract_id=50
                ),
            )
        )


def test_contract_currency_mismatch_rejected():
    contract = SimpleNamespace(
        status=ContractStatus.ACTIVE,
        currency_code="EUR",
    )

    db = FakeDB(
        [
            FakeResult(1),
            FakeResult(2),
            FakeResult(contract),
        ]
    )

    with pytest.raises(
        PaymentCurrencyError
    ):
        asyncio.run(
            revalidate_payment_references(
                db,
                payment=payment(
                    contract_id=50,
                    currency_code="UAH",
                ),
            )
        )


def test_create_draft(
    monkeypatch,
):
    db = FakeDB()

    revalidate = AsyncMock()

    monkeypatch.setattr(
        service,
        "revalidate_payment_references",
        revalidate,
    )

    result = asyncio.run(
        create_payment_draft(
            db,
            company_id=1,
            counterparty_id=2,
            contract_id=None,
            number=" PAY-100 ",
            direction=(
                PaymentDirection.INCOMING
            ),
            payment_date=date(
                2026,
                8,
                29,
            ),
            currency_code=" uah ",
            amount=Decimal("250.50"),
            created_by=3,
        )
    )

    assert (
        result.status
        == PaymentStatus.DRAFT
    )

    assert result.number == "PAY-100"
    assert result.currency_code == "UAH"

    assert (
        result.amount
        == Decimal("250.50")
    )

    assert db.added == [
        result
    ]

    revalidate.assert_awaited_once_with(
        db,
        payment=result,
    )

    db.flush.assert_awaited_once_with()


def test_create_requires_actor():
    db = FakeDB()

    with pytest.raises(
        PaymentActorError
    ):
        asyncio.run(
            create_payment_draft(
                db,
                company_id=1,
                counterparty_id=2,
                contract_id=None,
                number="PAY-1",
                direction=(
                    PaymentDirection.INCOMING
                ),
                payment_date=date(
                    2026,
                    8,
                    29,
                ),
                currency_code="UAH",
                amount=Decimal("1"),
                created_by=0,
            )
        )


def test_confirm_payment(
    monkeypatch,
):
    db = FakeDB()

    obj = payment()

    get_locked = AsyncMock(
        return_value=obj
    )

    revalidate = AsyncMock()

    monkeypatch.setattr(
        service,
        "get_locked_payment",
        get_locked,
    )

    monkeypatch.setattr(
        service,
        "revalidate_payment_references",
        revalidate,
    )

    result = asyncio.run(
        confirm_payment(
            db,
            company_id=1,
            payment_id=10,
            confirmed_by=99,
        )
    )

    assert result is obj

    assert (
        obj.status
        == PaymentStatus.CONFIRMED
    )

    assert (
        obj.confirmed_at
        is not None
    )

    assert obj.cancelled_at is None
    assert obj.cancelled_by is None

    revalidate.assert_awaited_once_with(
        db,
        payment=obj,
    )

    db.flush.assert_awaited_once_with()


@pytest.mark.parametrize(
    "initial_status",
    [
        PaymentStatus.DRAFT,
        PaymentStatus.CONFIRMED,
    ],
)
def test_cancel_payment(
    monkeypatch,
    initial_status,
):
    db = FakeDB()

    obj = payment(
        status=initial_status
    )

    get_locked = AsyncMock(
        return_value=obj
    )

    monkeypatch.setattr(
        service,
        "get_locked_payment",
        get_locked,
    )

    result = asyncio.run(
        cancel_payment(
            db,
            company_id=1,
            payment_id=10,
            cancelled_by=99,
        )
    )

    assert result is obj

    assert (
        obj.status
        == PaymentStatus.CANCELLED
    )

    assert obj.cancelled_by == 99

    assert (
        obj.cancelled_at
        is not None
    )

    db.flush.assert_awaited_once_with()


def test_cancel_requires_actor():
    db = FakeDB()

    with pytest.raises(
        PaymentActorError
    ):
        asyncio.run(
            cancel_payment(
                db,
                company_id=1,
                payment_id=10,
                cancelled_by=0,
            )
        )


@pytest.fixture(autouse=True)
def _default_no_active_settlement_allocations(
    monkeypatch,
):
    monkeypatch.setattr(
        service,
        "has_active_payment_settlement_allocations",
        AsyncMock(
            return_value=False
        ),
    )


def test_cancel_payment_blocked_by_active_settlement(
    monkeypatch,
):
    db = FakeDB()

    obj = payment(
        status=PaymentStatus.CONFIRMED
    )

    monkeypatch.setattr(
        service,
        "get_locked_payment",
        AsyncMock(
            return_value=obj
        ),
    )

    guard = AsyncMock(
        return_value=True
    )

    monkeypatch.setattr(
        service,
        "has_active_payment_settlement_allocations",
        guard,
    )

    with pytest.raises(
        PaymentStatusError,
        match="ACTIVE settlement",
    ):
        asyncio.run(
            cancel_payment(
                db,
                company_id=1,
                payment_id=10,
                cancelled_by=99,
            )
        )

    assert (
        obj.status
        == PaymentStatus.CONFIRMED
    )

    db.flush.assert_not_awaited()


@pytest.fixture(autouse=True)
def _default_payment_accounting_side_effects(
    monkeypatch,
):
    monkeypatch.setattr(
        service,
        "generate_and_post_payment_journal_entry",
        AsyncMock(),
    )

    monkeypatch.setattr(
        service,
        "reverse_payment_journal_entry",
        AsyncMock(),
    )
