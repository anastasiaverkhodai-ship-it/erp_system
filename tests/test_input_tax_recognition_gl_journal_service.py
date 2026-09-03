from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.services.tax_recognition_journal_service as service
from app.services.tax_recognition_journal_service import (
    TaxRecognitionJournalCurrencyError,
    TaxRecognitionJournalSourceStateError,
)


class _ScalarResult:
    def __init__(
        self,
        value=None,
    ):
        self.value = value

    def scalar_one_or_none(
        self,
    ):
        return self.value


class _Db:
    def __init__(
        self,
        scalar_value=None,
    ):
        self.scalar_value = scalar_value
        self.statements = []

    async def execute(
        self,
        statement,
    ):
        self.statements.append(
            statement
        )
        return _ScalarResult(
            self.scalar_value
        )


def _event(
    *,
    event_id=101,
    amount="20.00",
    reversal_of_id=None,
    currency_code="UAH",
    evidence_id=501,
    fulfillment_id=None,
    settlement_id=None,
):
    return SimpleNamespace(
        id=event_id,
        company_id=1,
        tax_calculation_id=301,
        invoice_fulfillment_allocation_id=(
            fulfillment_id
        ),
        payment_settlement_allocation_id=(
            settlement_id
        ),
        tax_credit_evidence_id=evidence_id,
        recognition_date=date(
            2026,
            8,
            15,
        ),
        recognized_tax_amount=Decimal(
            amount
        ),
        currency_code=currency_code,
        reversal_of_id=reversal_of_id,
    )


@pytest.mark.asyncio
async def test_original_input_recognition_builds_dr641_cr644_plan(
    monkeypatch,
):
    db = _Db()

    build_lines = AsyncMock(
        return_value=[]
    )
    post = AsyncMock(
        side_effect=(
            lambda db, *, journal_entry: journal_entry
        )
    )

    monkeypatch.setattr(
        service,
        "_build_journal_lines",
        build_lines,
    )
    monkeypatch.setattr(
        service,
        "_validate_and_post",
        post,
    )

    event = _event()

    entry = await (
        service
        .generate_and_post_input_vat_recognition_journal_entry(
            db,
            event=event,
            created_by=99,
        )
    )

    plan = (
        build_lines
        .await_args
        .kwargs["plan"]
    )

    rows = tuple(
        (
            line.role.value,
            line.debit,
            line.credit,
        )
        for line in plan.lines
    )

    assert rows == (
        (
            "tax_settlement",
            Decimal("20.00"),
            Decimal("0.00"),
        ),
        (
            "vat_input",
            Decimal("0.00"),
            Decimal("20.00"),
        ),
    )

    assert (
        entry.tax_recognition_event_id
        == event.id
    )
    assert (
        entry.entry_date
        == event.recognition_date
    )
    assert (
        entry.description
        == "INPUT VAT Recognition event 101"
    )


@pytest.mark.asyncio
async def test_input_recognition_requires_evidence_only_source():
    db = _Db()

    event = _event(
        settlement_id=700,
    )

    with pytest.raises(
        TaxRecognitionJournalSourceStateError,
        match="TaxCreditEvidence as its only typed source",
    ):
        await (
            service
            .generate_and_post_input_vat_recognition_journal_entry(
                db,
                event=event,
                created_by=99,
            )
        )


@pytest.mark.asyncio
async def test_input_recognition_requires_positive_evidence_id():
    db = _Db()

    event = _event(
        evidence_id=0,
    )

    with pytest.raises(
        TaxRecognitionJournalSourceStateError,
        match="Evidence source must be greater than zero",
    ):
        await (
            service
            .generate_and_post_input_vat_recognition_journal_entry(
                db,
                event=event,
                created_by=99,
            )
        )


@pytest.mark.asyncio
async def test_input_recognition_rejects_non_uah():
    db = _Db()

    with pytest.raises(
        TaxRecognitionJournalCurrencyError,
        match="supports UAH only",
    ):
        await (
            service
            .generate_and_post_input_vat_recognition_journal_entry(
                db,
                event=_event(
                    currency_code="EUR",
                ),
                created_by=99,
            )
        )


@pytest.mark.asyncio
async def test_zero_input_recognition_is_gl_noop():
    db = _Db()

    result = await (
        service
        .generate_and_post_input_vat_recognition_journal_entry(
            db,
            event=_event(
                amount="0.00",
            ),
            created_by=99,
        )
    )

    assert result is None
    assert db.statements == []


@pytest.mark.asyncio
async def test_input_reversal_uses_generic_typed_source_override(
    monkeypatch,
):
    db = _Db()

    original = SimpleNamespace(
        id=900
    )

    get_original = AsyncMock(
        return_value=original
    )
    reverse = AsyncMock(
        return_value=SimpleNamespace(
            id=901
        )
    )

    monkeypatch.setattr(
        service,
        "get_original_input_vat_recognition_journal_entry",
        get_original,
    )
    monkeypatch.setattr(
        service,
        "reverse_journal_entry",
        reverse,
    )

    reversal_event = _event(
        event_id=102,
        reversal_of_id=101,
    )

    result = await (
        service
        .reverse_input_vat_recognition_journal_entry(
            db,
            reversal_event=reversal_event,
            reversed_by=99,
        )
    )

    assert result.id == 901

    get_original.assert_awaited_once_with(
        db,
        company_id=1,
        tax_recognition_event_id=101,
        lock=True,
    )

    reverse.assert_awaited_once()

    kwargs = (
        reverse
        .await_args
        .kwargs
    )

    assert (
        kwargs["journal_entry_id"]
        == 900
    )
    assert (
        kwargs["tax_recognition_event_id_override"]
        == 102
    )
    assert (
        kwargs["reversal_date"]
        == reversal_event.recognition_date
    )
