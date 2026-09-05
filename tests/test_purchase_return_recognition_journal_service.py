import asyncio
from functools import wraps

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.models.journal_entry import (
    JournalEntry,
    JournalEntryStatus,
)
from app.models.purchase_return_recognition_event import (
    PurchaseReturnRecognitionEvent,
)
from app.services.accounting_account_roles import (
    AccountingAccountRole,
)
import app.services.purchase_return_recognition_journal_service as service


def _run_async_test(func):
    """
    Run an async unit-test body without requiring a pytest async plugin.
    """
    @wraps(func)
    def wrapper(
        *args,
        **kwargs,
    ):
        return asyncio.run(
            func(
                *args,
                **kwargs,
            )
        )

    return wrapper


class _Result:
    def __init__(
        self,
        value,
    ):
        self.value = value

    def scalar_one_or_none(
        self,
    ):
        return self.value


class _Session:
    def __init__(
        self,
        results=(),
    ):
        self.results = list(
            results
        )
        self.added = []
        self.flush_count = 0

    async def execute(
        self,
        statement,
    ):
        if not self.results:
            raise AssertionError(
                "Unexpected execute()"
            )

        return _Result(
            self.results.pop(0)
        )

    def add(
        self,
        value,
    ):
        self.added.append(
            value
        )

    async def flush(
        self,
    ):
        self.flush_count += 1

        for item in self.added:
            if (
                isinstance(
                    item,
                    JournalEntry,
                )
                and item.id is None
            ):
                item.id = 900

    async def commit(
        self,
    ):
        raise AssertionError(
            "service must not commit"
        )

    async def rollback(
        self,
    ):
        raise AssertionError(
            "service must not rollback"
        )


def _event(
    *,
    event_id=10,
    base="5.00",
    reversal_of_id=None,
    currency="UAH",
):
    return PurchaseReturnRecognitionEvent(
        id=event_id,
        company_id=1,
        trade_return_event_id=20,
        invoice_fulfillment_allocation_id=30,
        recognition_date=date(
            2026,
            9,
            5,
        ),
        returned_quantity=Decimal("1"),
        returned_base_amount=Decimal(
            base
        ),
        returned_gross_amount=Decimal("6.00"),
        returned_tax_amount=Decimal("1.00"),
        currency_code=currency,
        created_by=1,
        reversal_of_id=reversal_of_id,
    )


async def _resolver(
    db,
    *,
    company_id,
    roles,
):
    assert company_id == 1

    assert tuple(
        roles
    ) == (
        AccountingAccountRole.SUPPLIER_PAYABLES,
        AccountingAccountRole.INVENTORY_GOODS,
    )

    return {
        AccountingAccountRole.SUPPLIER_PAYABLES:
            SimpleNamespace(
                id=631,
            ),
        AccountingAccountRole.INVENTORY_GOODS:
            SimpleNamespace(
                id=281,
            ),
    }


async def _validate(
    *,
    db,
    journal_entry,
):
    assert journal_entry.lines


@_run_async_test
async def test_positive_original_posts_dr631_cr281(
    monkeypatch,
):
    db = _Session(
        results=(
            None,
        )
    )

    monkeypatch.setattr(
        service,
        "resolve_company_account_roles",
        _resolver,
    )

    monkeypatch.setattr(
        service,
        "validate_journal_entry",
        _validate,
    )

    async def post(
        *,
        db,
        company_id,
        journal_entry_id,
    ):
        assert company_id == 1
        assert journal_entry_id == 900

        return db.added[-1]

    monkeypatch.setattr(
        service,
        "post_journal_entry",
        post,
    )

    result = await (
        service
        .generate_and_post_purchase_return_recognition_journal_entry(
            db,
            event=_event(
                base="5.00",
            ),
            created_by=1,
        )
    )

    assert result is db.added[-1]
    assert (
        result.purchase_return_recognition_event_id
        == 10
    )
    assert result.entry_date == date(
        2026,
        9,
        5,
    )

    assert len(
        result.lines
    ) == 2

    assert result.lines[0].account_id == 631
    assert result.lines[0].debit == Decimal("5.00")
    assert result.lines[0].credit == Decimal("0")

    assert result.lines[1].account_id == 281
    assert result.lines[1].debit == Decimal("0")
    assert result.lines[1].credit == Decimal("5.00")


@_run_async_test
async def test_zero_base_original_creates_no_journal(
    monkeypatch,
):
    db = _Session(
        results=(
            None,
        )
    )

    async def forbidden_resolver(
        *args,
        **kwargs,
    ):
        raise AssertionError(
            "zero event must not resolve accounts"
        )

    monkeypatch.setattr(
        service,
        "resolve_company_account_roles",
        forbidden_resolver,
    )

    result = await (
        service
        .generate_and_post_purchase_return_recognition_journal_entry(
            db,
            event=_event(
                base="0.00",
            ),
            created_by=1,
        )
    )

    assert result is None
    assert db.added == []
    assert db.flush_count == 0


@_run_async_test
async def test_duplicate_original_is_rejected():
    db = _Session(
        results=(
            777,
        )
    )

    with pytest.raises(
        service.PurchaseReturnRecognitionJournalDuplicateError
    ):
        await (
            service
            .generate_and_post_purchase_return_recognition_journal_entry(
                db,
                event=_event(),
                created_by=1,
            )
        )


@_run_async_test
async def test_non_uah_original_is_rejected_before_query():
    db = _Session()

    with pytest.raises(
        service.PurchaseReturnRecognitionJournalCurrencyError
    ):
        await (
            service
            .generate_and_post_purchase_return_recognition_journal_entry(
                db,
                event=_event(
                    currency="EUR",
                ),
                created_by=1,
            )
        )


@_run_async_test
async def test_positive_reversal_uses_typed_override(
    monkeypatch,
):
    original = JournalEntry(
        id=101,
        company_id=1,
        purchase_return_recognition_event_id=10,
        accounting_rule_id=None,
        entry_date=date(
            2026,
            9,
            1,
        ),
        status=JournalEntryStatus.POSTED,
        created_by=1,
    )

    db = _Session(
        results=(
            None,
            original,
        )
    )

    captured = {}

    async def reverse(
        *,
        db,
        company_id,
        journal_entry_id,
        reversal_date,
        reversed_by,
        purchase_return_recognition_event_id_override=None,
        **kwargs,
    ):
        captured.update(
            {
                "company_id": company_id,
                "journal_entry_id": journal_entry_id,
                "reversal_date": reversal_date,
                "reversed_by": reversed_by,
                "override": (
                    purchase_return_recognition_event_id_override
                ),
            }
        )

        return SimpleNamespace(
            id=202
        )

    monkeypatch.setattr(
        service,
        "reverse_journal_entry",
        reverse,
    )

    result = await (
        service
        .reverse_purchase_return_recognition_journal_entry(
            db,
            reversal_event=_event(
                event_id=11,
                base="5.00",
                reversal_of_id=10,
            ),
            reversed_by=1,
        )
    )

    assert result.id == 202

    assert captured == {
        "company_id": 1,
        "journal_entry_id": 101,
        "reversal_date": date(
            2026,
            9,
            5,
        ),
        "reversed_by": 1,
        "override": 11,
    }


@_run_async_test
async def test_zero_base_reversal_creates_no_journal_and_never_loads_original(
    monkeypatch,
):
    db = _Session(
        results=(
            None,
        )
    )

    async def forbidden_reverse(
        *args,
        **kwargs,
    ):
        raise AssertionError(
            "zero reversal must not reverse a JE"
        )

    monkeypatch.setattr(
        service,
        "reverse_journal_entry",
        forbidden_reverse,
    )

    result = await (
        service
        .reverse_purchase_return_recognition_journal_entry(
            db,
            reversal_event=_event(
                event_id=11,
                base="0.00",
                reversal_of_id=10,
            ),
            reversed_by=1,
        )
    )

    assert result is None
    assert db.results == []


@_run_async_test
async def test_reversal_event_cannot_generate_original():
    db = _Session()

    with pytest.raises(
        service.PurchaseReturnRecognitionJournalSourceStateError
    ):
        await (
            service
            .generate_and_post_purchase_return_recognition_journal_entry(
                db,
                event=_event(
                    event_id=11,
                    reversal_of_id=10,
                ),
                created_by=1,
            )
        )


@_run_async_test
async def test_original_event_cannot_reverse_journal():
    db = _Session()

    with pytest.raises(
        service.PurchaseReturnRecognitionJournalSourceStateError
    ):
        await (
            service
            .reverse_purchase_return_recognition_journal_entry(
                db,
                reversal_event=_event(),
                reversed_by=1,
            )
        )


def test_returned_gross_and_tax_do_not_determine_gl_amount():
    event = _event(
        base="0.02",
    )

    event.returned_gross_amount = Decimal("999.99")
    event.returned_tax_amount = Decimal("123.45")

    assert (
        service._event_amount(
            event
        )
        == Decimal("0.02")
    )
