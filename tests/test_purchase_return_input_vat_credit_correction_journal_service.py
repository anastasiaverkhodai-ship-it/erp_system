import ast
import inspect
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

import app.services.purchase_return_input_vat_credit_correction_journal_service as service
from app.models.journal_entry import (
    JournalEntry,
)
from app.models.purchase_return_input_vat_credit_correction_event import (
    PurchaseReturnInputVatCreditCorrectionEvent,
)
from app.services.accounting_reversal import (
    AccountingReversalError,
    _resolve_reversal_purchase_return_input_vat_credit_correction_event_id,
)
from app.services.purchase_return_input_vat_credit_correction_journal_service import (
    PurchaseReturnInputVatCreditCorrectionJournalCurrencyError,
    PurchaseReturnInputVatCreditCorrectionJournalDuplicateError,
    PurchaseReturnInputVatCreditCorrectionJournalSourceStateError,
    generate_and_post_purchase_return_input_vat_credit_correction_journal_entry,
    reverse_purchase_return_input_vat_credit_correction_journal_entry,
)


D1 = date(
    2026,
    9,
    5,
)

D2 = date(
    2026,
    9,
    6,
)


def event(
    event_id=10,
    *,
    base="25.00",
    tax="5.00",
    adjustment_date=D1,
    currency="UAH",
    reversal_of_id=None,
):
    return (
        PurchaseReturnInputVatCreditCorrectionEvent(
            id=event_id,
            company_id=1,
            purchase_return_vat_adjustment_event_id=30,
            tax_calculation_id=20,
            adjustment_date=adjustment_date,
            reduced_taxable_base=Decimal(
                base
            ),
            reduced_tax_amount=Decimal(
                tax
            ),
            currency_code=currency,
            created_by=7,
            reversal_of_id=reversal_of_id,
        )
    )


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


class _DB:
    def __init__(
        self,
        values=(),
    ):
        self.values = list(
            values
        )

        self.execute_count = 0

    async def execute(
        self,
        statement,
    ):
        self.execute_count += 1

        if not self.values:
            raise AssertionError(
                "Unexpected DB execute"
            )

        return _Result(
            self.values.pop(
                0
            )
        )


@pytest.mark.asyncio
async def test_original_journal_has_immutable_typed_source_and_date(
    monkeypatch,
):
    db = _DB(
        values=(
            None,
        )
    )

    async def fake_lines(
        db,
        *,
        company_id,
        plan,
        description,
    ):
        return []

    async def fake_post(
        db,
        *,
        journal_entry,
    ):
        return journal_entry

    monkeypatch.setattr(
        service,
        "_build_journal_lines",
        fake_lines,
    )

    monkeypatch.setattr(
        service,
        "_validate_and_post",
        fake_post,
    )

    result = (
        await generate_and_post_purchase_return_input_vat_credit_correction_journal_entry(
            db,
            event=event(
                base="999.00",
                tax="3.00",
            ),
            created_by=9,
        )
    )

    assert isinstance(
        result,
        JournalEntry,
    )

    assert (
        result
        .purchase_return_input_vat_credit_correction_event_id
        == 10
    )

    assert (
        result
        .purchase_return_vat_adjustment_event_id
        is None
    )

    assert (
        result.entry_date
        == D1
    )


@pytest.mark.asyncio
async def test_zero_tax_original_creates_no_journal():
    db = _DB()

    result = (
        await generate_and_post_purchase_return_input_vat_credit_correction_journal_entry(
            db,
            event=event(
                base="25.00",
                tax="0.00",
            ),
            created_by=9,
        )
    )

    assert result is None
    assert db.execute_count == 0


@pytest.mark.asyncio
async def test_duplicate_original_is_rejected():
    db = _DB(
        values=(
            999,
        )
    )

    with pytest.raises(
        PurchaseReturnInputVatCreditCorrectionJournalDuplicateError
    ):
        await generate_and_post_purchase_return_input_vat_credit_correction_journal_entry(
            db,
            event=event(),
            created_by=9,
        )


@pytest.mark.asyncio
async def test_reversal_event_cannot_generate_original_journal():
    db = _DB()

    with pytest.raises(
        PurchaseReturnInputVatCreditCorrectionJournalSourceStateError
    ):
        await generate_and_post_purchase_return_input_vat_credit_correction_journal_entry(
            db,
            event=event(
                event_id=11,
                adjustment_date=D2,
                reversal_of_id=10,
            ),
            created_by=9,
        )


@pytest.mark.asyncio
async def test_non_uah_original_is_rejected():
    db = _DB()

    with pytest.raises(
        PurchaseReturnInputVatCreditCorrectionJournalCurrencyError
    ):
        await generate_and_post_purchase_return_input_vat_credit_correction_journal_entry(
            db,
            event=event(
                currency="EUR"
            ),
            created_by=9,
        )


@pytest.mark.asyncio
async def test_positive_reversal_uses_reversal_event_as_typed_source(
    monkeypatch,
):
    original = SimpleNamespace(
        id=500,
    )

    async def fake_get(
        db,
        *,
        company_id,
        purchase_return_input_vat_credit_correction_event_id,
        lock,
    ):
        assert company_id == 1

        assert (
            purchase_return_input_vat_credit_correction_event_id
            == 10
        )

        assert lock is True

        return original

    captured = {}

    async def fake_reverse(
        **kwargs,
    ):
        captured.update(
            kwargs
        )

        return "reversed"

    monkeypatch.setattr(
        service,
        "get_original_purchase_return_input_vat_credit_correction_journal_entry",
        fake_get,
    )

    monkeypatch.setattr(
        service,
        "reverse_journal_entry",
        fake_reverse,
    )

    result = (
        await reverse_purchase_return_input_vat_credit_correction_journal_entry(
            object(),
            reversal_event=event(
                event_id=11,
                adjustment_date=D2,
                reversal_of_id=10,
            ),
            reversed_by=9,
        )
    )

    assert result == "reversed"

    assert (
        captured[
            "journal_entry_id"
        ]
        == 500
    )

    assert (
        captured[
            "reversal_date"
        ]
        == D2
    )

    assert (
        captured[
            "purchase_return_input_vat_credit_correction_event_id_override"
        ]
        == 11
    )


@pytest.mark.asyncio
async def test_zero_tax_reversal_creates_no_journal():
    db = _DB(
        values=(
            None,
        )
    )

    result = (
        await reverse_purchase_return_input_vat_credit_correction_journal_entry(
            db,
            reversal_event=event(
                event_id=11,
                base="25.00",
                tax="0.00",
                adjustment_date=D2,
                reversal_of_id=10,
            ),
            reversed_by=9,
        )
    )

    assert result is None


@pytest.mark.asyncio
async def test_original_event_cannot_be_dispatched_as_reversal():
    with pytest.raises(
        PurchaseReturnInputVatCreditCorrectionJournalSourceStateError
    ):
        await reverse_purchase_return_input_vat_credit_correction_journal_entry(
            object(),
            reversal_event=event(),
            reversed_by=9,
        )


def test_reversal_helper_preserves_original_without_override():
    assert (
        _resolve_reversal_purchase_return_input_vat_credit_correction_event_id(
            original_purchase_return_input_vat_credit_correction_event_id=10,
            override_purchase_return_input_vat_credit_correction_event_id=None,
        )
        == 10
    )


def test_reversal_helper_accepts_reversal_event_override():
    assert (
        _resolve_reversal_purchase_return_input_vat_credit_correction_event_id(
            original_purchase_return_input_vat_credit_correction_event_id=10,
            override_purchase_return_input_vat_credit_correction_event_id=11,
        )
        == 11
    )


def test_reversal_helper_requires_original_typed_source():
    with pytest.raises(
        AccountingReversalError
    ):
        _resolve_reversal_purchase_return_input_vat_credit_correction_event_id(
            original_purchase_return_input_vat_credit_correction_event_id=None,
            override_purchase_return_input_vat_credit_correction_event_id=11,
        )


def test_reversal_helper_rejects_nonpositive_override():
    with pytest.raises(
        AccountingReversalError
    ):
        _resolve_reversal_purchase_return_input_vat_credit_correction_event_id(
            original_purchase_return_input_vat_credit_correction_event_id=10,
            override_purchase_return_input_vat_credit_correction_event_id=0,
        )


def test_journal_uses_reduced_tax_amount_only():
    source = inspect.getsource(
        service
    )

    tree = ast.parse(
        source
    )

    attributes = {
        node.attr
        for node in ast.walk(
            tree
        )
        if isinstance(
            node,
            ast.Attribute,
        )
    }

    assert (
        "reduced_tax_amount"
        in attributes
    )

    assert (
        "reduced_taxable_base"
        not in attributes
    )


def test_journal_service_has_no_commit_or_rollback():
    source = inspect.getsource(
        service
    )

    tree = ast.parse(
        source
    )

    calls = {
        node.func.attr
        for node in ast.walk(
            tree
        )
        if (
            isinstance(
                node,
                ast.Call,
            )
            and isinstance(
                node.func,
                ast.Attribute,
            )
        )
    }

    assert "commit" not in calls
    assert "rollback" not in calls
