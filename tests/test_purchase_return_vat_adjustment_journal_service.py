import inspect
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
)

import app.services.purchase_return_vat_adjustment_journal_service as service
from app.models.journal_entry import (
    JournalEntry,
    JournalEntryStatus,
)
from app.models.journal_entry_line import (
    JournalEntryLine,
)
from app.models.purchase_return_vat_adjustment_event import (
    PurchaseReturnVatAdjustmentEvent,
)
from app.services.accounting_reversal import (
    AccountingReversalError,
    _resolve_reversal_purchase_return_vat_adjustment_event_id,
    reverse_journal_entry,
)
from app.services.purchase_return_vat_adjustment_journal_service import (
    PurchaseReturnVatAdjustmentJournalCurrencyError,
    PurchaseReturnVatAdjustmentJournalSourceStateError,
    generate_and_post_purchase_return_vat_adjustment_journal_entry,
    reverse_purchase_return_vat_adjustment_journal_entry,
)


D1 = date(2026, 9, 5)
D2 = date(2026, 9, 6)


def event(
    event_id=100,
    *,
    tax="20.00",
    reversal_of_id=None,
    adjustment_date=D1,
    currency_code="UAH",
):
    return PurchaseReturnVatAdjustmentEvent(
        id=event_id,
        company_id=1,
        purchase_return_recognition_event_id=10,
        tax_calculation_id=20,
        adjustment_date=adjustment_date,
        basis_kind="goods_received_by_supplier",
        adjusted_taxable_base=Decimal("100.00"),
        adjusted_tax_amount=Decimal(tax),
        currency_code=currency_code,
        created_by=7,
        reversal_of_id=reversal_of_id,
    )


class _Result:
    def __init__(
        self,
        value=None,
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


def test_journal_entry_has_typed_source():
    column = (
        JournalEntry.__table__.columns[
            "purchase_return_vat_adjustment_event_id"
        ]
    )

    assert column.nullable is True


def test_journal_entry_has_composite_fk():
    constraint = next(
        item
        for item
        in JournalEntry.__table__.constraints
        if (
            isinstance(
                item,
                ForeignKeyConstraint,
            )
            and item.name
            == (
                "fk_journal_entries_company_"
                "purchase_return_vat_adjustment_event"
            )
        )
    )

    assert tuple(
        column.name
        for column in constraint.columns
    ) == (
        "company_id",
        "purchase_return_vat_adjustment_event_id",
    )

    assert tuple(
        element.target_fullname
        for element in constraint.elements
    ) == (
        "purchase_return_vat_adjustment_events.company_id",
        "purchase_return_vat_adjustment_events.id",
    )

    assert constraint.ondelete == "RESTRICT"


def test_business_source_check_has_new_pairwise_source():
    constraint = next(
        item
        for item
        in JournalEntry.__table__.constraints
        if (
            isinstance(
                item,
                CheckConstraint,
            )
            and item.name
            == (
                "ck_journal_entries_"
                "at_most_one_business_source"
            )
        )
    )

    sql = " ".join(
        str(
            constraint.sqltext
        ).split()
    )

    old_sources = (
        "document_id",
        "payment_id",
        "payment_settlement_allocation_id",
        "tax_recognition_event_id",
        "sales_recognition_event_id",
        "vat_advance_bridge_event_id",
        "input_vat_fulfillment_bridge_event_id",
        "supplier_advance_clearing_event_id",
        "customer_advance_clearing_event_id",
        "sales_return_recognition_event_id",
        "sales_return_cost_restoration_event_id",
        "purchase_return_recognition_event_id",
    )

    for source in old_sources:
        assert (
            f"{source} IS NULL "
            "OR purchase_return_vat_adjustment_event_id IS NULL"
        ) in sql


def test_partial_unique_original_index_exists():
    index = next(
        item
        for item
        in JournalEntry.__table__.indexes
        if item.name
        == (
            "uq_journal_entry_original_"
            "purchase_return_vat_adjustment_event"
        )
    )

    assert index.unique is True

    assert tuple(
        column.name
        for column in index.columns
    ) == (
        "purchase_return_vat_adjustment_event_id",
    )


def test_generic_reversal_has_typed_override():
    assert (
        "purchase_return_vat_adjustment_event_id_override"
        in inspect.signature(
            reverse_journal_entry
        ).parameters
    )


def test_reversal_resolver_copies_original():
    assert (
        _resolve_reversal_purchase_return_vat_adjustment_event_id(
            original_purchase_return_vat_adjustment_event_id=100,
            override=None,
        )
        == 100
    )


def test_reversal_resolver_uses_override():
    assert (
        _resolve_reversal_purchase_return_vat_adjustment_event_id(
            original_purchase_return_vat_adjustment_event_id=100,
            override=101,
        )
        == 101
    )


def test_reversal_override_requires_original_source():
    with pytest.raises(
        AccountingReversalError
    ):
        _resolve_reversal_purchase_return_vat_adjustment_event_id(
            original_purchase_return_vat_adjustment_event_id=None,
            override=101,
        )


@pytest.mark.asyncio
async def test_original_positive_tax_posts_dr_631_cr_644(
    monkeypatch,
):
    db = _Session(
        results=(
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
        return [
            JournalEntryLine(
                line_no=1,
                account_id=631,
                debit=Decimal("20.00"),
                credit=Decimal("0"),
                description=description,
            ),
            JournalEntryLine(
                line_no=2,
                account_id=644,
                debit=Decimal("0"),
                credit=Decimal("20.00"),
                description=description,
            ),
        ]

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
        await generate_and_post_purchase_return_vat_adjustment_journal_entry(
            db,
            event=event(),
            created_by=9,
        )
    )

    assert result is not None

    assert (
        result.purchase_return_vat_adjustment_event_id
        == 100
    )

    assert result.entry_date == D1
    assert result.status == JournalEntryStatus.DRAFT

    assert (
        result.lines[
            0
        ].debit
        == Decimal("20.00")
    )

    assert (
        result.lines[
            1
        ].credit
        == Decimal("20.00")
    )


@pytest.mark.asyncio
async def test_zero_tax_event_creates_no_journal():
    result = (
        await generate_and_post_purchase_return_vat_adjustment_journal_entry(
            object(),
            event=event(
                tax="0.00"
            ),
            created_by=9,
        )
    )

    assert result is None


@pytest.mark.asyncio
async def test_reversal_event_cannot_create_original_journal():
    with pytest.raises(
        PurchaseReturnVatAdjustmentJournalSourceStateError
    ):
        await generate_and_post_purchase_return_vat_adjustment_journal_entry(
            object(),
            event=event(
                event_id=101,
                reversal_of_id=100,
            ),
            created_by=9,
        )


@pytest.mark.asyncio
async def test_non_uah_fails_closed():
    with pytest.raises(
        PurchaseReturnVatAdjustmentJournalCurrencyError
    ):
        await generate_and_post_purchase_return_vat_adjustment_journal_entry(
            object(),
            event=event(
                currency_code="EUR"
            ),
            created_by=9,
        )


@pytest.mark.asyncio
async def test_positive_tax_reversal_uses_typed_override(
    monkeypatch,
):
    original_je = JournalEntry(
        id=500,
        company_id=1,
        purchase_return_vat_adjustment_event_id=100,
        entry_date=D1,
        status=JournalEntryStatus.POSTED,
        created_by=7,
    )

    async def fake_get(
        db,
        *,
        company_id,
        purchase_return_vat_adjustment_event_id,
        lock,
    ):
        assert company_id == 1
        assert (
            purchase_return_vat_adjustment_event_id
            == 100
        )
        assert lock is True

        return original_je

    captured = {}

    async def fake_reverse(
        **kwargs,
    ):
        captured.update(
            kwargs
        )

        return JournalEntry(
            id=501,
            company_id=1,
            purchase_return_vat_adjustment_event_id=101,
            entry_date=D2,
            status=JournalEntryStatus.POSTED,
            created_by=9,
            reversal_of_id=500,
        )

    monkeypatch.setattr(
        service,
        "get_original_purchase_return_vat_adjustment_journal_entry",
        fake_get,
    )

    monkeypatch.setattr(
        service,
        "reverse_journal_entry",
        fake_reverse,
    )

    result = (
        await reverse_purchase_return_vat_adjustment_journal_entry(
            object(),
            reversal_event=event(
                event_id=101,
                reversal_of_id=100,
                adjustment_date=D2,
            ),
            reversed_by=9,
        )
    )

    assert result is not None

    assert (
        captured[
            "purchase_return_vat_adjustment_event_id_override"
        ]
        == 101
    )

    assert (
        captured[
            "reversal_date"
        ]
        == D2
    )


@pytest.mark.asyncio
async def test_zero_tax_reversal_creates_no_journal():
    db = _Session(
        results=(
            None,
        )
    )

    result = (
        await reverse_purchase_return_vat_adjustment_journal_entry(
            db,
            reversal_event=event(
                event_id=101,
                tax="0.00",
                reversal_of_id=100,
                adjustment_date=D2,
            ),
            reversed_by=9,
        )
    )

    assert result is None


def test_service_has_no_commit_or_rollback():
    source = inspect.getsource(
        service
    )

    assert ".commit(" not in source
    assert ".rollback(" not in source
