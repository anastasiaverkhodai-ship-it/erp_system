import ast
import inspect
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock

import pytest

import app.services.purchase_return_input_vat_credit_correction_lifecycle_service as service
from app.models.purchase_return_input_vat_credit_correction_event import (
    PurchaseReturnInputVatCreditCorrectionEvent,
)
from app.services.purchase_return_input_vat_credit_correction_journal_service import (
    PurchaseReturnInputVatCreditCorrectionJournalError,
)
from app.services.purchase_return_input_vat_credit_correction_lifecycle_service import (
    PurchaseReturnInputVatCreditCorrectionLifecycleError,
    reconcile_purchase_return_input_vat_credit_correction_lifecycle_for_tax_calculation,
)
from app.services.purchase_return_input_vat_credit_correction_reconciliation_service import (
    PurchaseReturnInputVatCreditCorrectionReconciliationError,
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
    event_id,
    *,
    reversal_of_id=None,
    tax="5.00",
):
    return (
        PurchaseReturnInputVatCreditCorrectionEvent(
            id=event_id,
            company_id=1,
            purchase_return_vat_adjustment_event_id=30,
            tax_calculation_id=20,
            adjustment_date=D2,
            reduced_taxable_base=Decimal(
                "25.00"
            ),
            reduced_tax_amount=Decimal(
                tax
            ),
            currency_code="UAH",
            created_by=7,
            reversal_of_id=reversal_of_id,
        )
    )


def result(
    *events,
):
    return SimpleNamespace(
        created_events=tuple(
            events
        )
    )


@pytest.mark.asyncio
async def test_lifecycle_consumes_created_events_in_exact_order(
    monkeypatch,
):
    reversal = event(
        11,
        reversal_of_id=10,
    )

    replacement = event(
        12,
    )

    zero_tax_original = event(
        13,
        tax="0.00",
    )

    expected = result(
        reversal,
        replacement,
        zero_tax_original,
    )

    reconcile = AsyncMock(
        return_value=expected
    )

    order = []

    async def post_original(
        db,
        *,
        event,
        created_by,
    ):
        order.append(
            (
                "original",
                event.id,
                created_by,
            )
        )

        return None

    async def post_reversal(
        db,
        *,
        reversal_event,
        reversed_by,
    ):
        order.append(
            (
                "reversal",
                reversal_event.id,
                reversed_by,
            )
        )

        return None

    monkeypatch.setattr(
        service,
        (
            "reconcile_purchase_return_input_vat_"
            "credit_corrections_for_tax_calculation"
        ),
        reconcile,
    )

    monkeypatch.setattr(
        service,
        (
            "generate_and_post_purchase_return_input_vat_"
            "credit_correction_journal_entry"
        ),
        post_original,
    )

    monkeypatch.setattr(
        service,
        (
            "reverse_purchase_return_input_vat_credit_"
            "correction_journal_entry"
        ),
        post_reversal,
    )

    actual = (
        await reconcile_purchase_return_input_vat_credit_correction_lifecycle_for_tax_calculation(
            object(),
            company_id=1,
            tax_calculation_id=20,
            adjustment_date=D2,
            created_by=9,
        )
    )

    assert actual is expected

    assert order == [
        (
            "reversal",
            11,
            9,
        ),
        (
            "original",
            12,
            9,
        ),
        (
            "original",
            13,
            9,
        ),
    ]

    reconcile.assert_awaited_once_with(
        ANY,
        company_id=1,
        tax_calculation_id=20,
        adjustment_date=D2,
        created_by=9,
    )


@pytest.mark.asyncio
async def test_no_created_events_dispatches_no_journal(
    monkeypatch,
):
    expected = result()

    monkeypatch.setattr(
        service,
        (
            "reconcile_purchase_return_input_vat_"
            "credit_corrections_for_tax_calculation"
        ),
        AsyncMock(
            return_value=expected
        ),
    )

    post_original = AsyncMock()
    post_reversal = AsyncMock()

    monkeypatch.setattr(
        service,
        (
            "generate_and_post_purchase_return_input_vat_"
            "credit_correction_journal_entry"
        ),
        post_original,
    )

    monkeypatch.setattr(
        service,
        (
            "reverse_purchase_return_input_vat_credit_"
            "correction_journal_entry"
        ),
        post_reversal,
    )

    actual = (
        await reconcile_purchase_return_input_vat_credit_correction_lifecycle_for_tax_calculation(
            object(),
            company_id=1,
            tax_calculation_id=20,
            adjustment_date=D1,
            created_by=9,
        )
    )

    assert actual is expected

    post_original.assert_not_awaited()
    post_reversal.assert_not_awaited()


@pytest.mark.asyncio
async def test_reconciliation_error_is_wrapped(
    monkeypatch,
):
    async def fail(
        *args,
        **kwargs,
    ):
        raise (
            PurchaseReturnInputVatCreditCorrectionReconciliationError(
                "boom"
            )
        )

    monkeypatch.setattr(
        service,
        (
            "reconcile_purchase_return_input_vat_"
            "credit_corrections_for_tax_calculation"
        ),
        fail,
    )

    with pytest.raises(
        PurchaseReturnInputVatCreditCorrectionLifecycleError,
        match="reconciliation failed",
    ):
        await reconcile_purchase_return_input_vat_credit_correction_lifecycle_for_tax_calculation(
            object(),
            company_id=1,
            tax_calculation_id=20,
            adjustment_date=D1,
            created_by=9,
        )


@pytest.mark.asyncio
async def test_original_journal_error_is_wrapped(
    monkeypatch,
):
    original = event(
        12
    )

    monkeypatch.setattr(
        service,
        (
            "reconcile_purchase_return_input_vat_"
            "credit_corrections_for_tax_calculation"
        ),
        AsyncMock(
            return_value=result(
                original
            )
        ),
    )

    async def fail_original(
        db,
        *,
        event,
        created_by,
    ):
        raise (
            PurchaseReturnInputVatCreditCorrectionJournalError(
                "posting failed"
            )
        )

    monkeypatch.setattr(
        service,
        (
            "generate_and_post_purchase_return_input_vat_"
            "credit_correction_journal_entry"
        ),
        fail_original,
    )

    with pytest.raises(
        PurchaseReturnInputVatCreditCorrectionLifecycleError,
        match="journal dispatch failed",
    ):
        await reconcile_purchase_return_input_vat_credit_correction_lifecycle_for_tax_calculation(
            object(),
            company_id=1,
            tax_calculation_id=20,
            adjustment_date=D1,
            created_by=9,
        )


@pytest.mark.asyncio
async def test_reversal_journal_error_is_wrapped(
    monkeypatch,
):
    reversal = event(
        11,
        reversal_of_id=10,
    )

    monkeypatch.setattr(
        service,
        (
            "reconcile_purchase_return_input_vat_"
            "credit_corrections_for_tax_calculation"
        ),
        AsyncMock(
            return_value=result(
                reversal
            )
        ),
    )

    async def fail_reversal(
        db,
        *,
        reversal_event,
        reversed_by,
    ):
        raise (
            PurchaseReturnInputVatCreditCorrectionJournalError(
                "reversal failed"
            )
        )

    monkeypatch.setattr(
        service,
        (
            "reverse_purchase_return_input_vat_credit_"
            "correction_journal_entry"
        ),
        fail_reversal,
    )

    with pytest.raises(
        PurchaseReturnInputVatCreditCorrectionLifecycleError,
        match="journal dispatch failed",
    ):
        await reconcile_purchase_return_input_vat_credit_correction_lifecycle_for_tax_calculation(
            object(),
            company_id=1,
            tax_calculation_id=20,
            adjustment_date=D1,
            created_by=9,
        )


@pytest.mark.parametrize(
    (
        "company_id",
        "tax_calculation_id",
        "created_by",
    ),
    (
        (
            0,
            20,
            9,
        ),
        (
            1,
            0,
            9,
        ),
        (
            1,
            20,
            0,
        ),
        (
            True,
            20,
            9,
        ),
    ),
)
@pytest.mark.asyncio
async def test_lifecycle_validates_positive_ids(
    company_id,
    tax_calculation_id,
    created_by,
):
    with pytest.raises(
        PurchaseReturnInputVatCreditCorrectionLifecycleError
    ):
        await reconcile_purchase_return_input_vat_credit_correction_lifecycle_for_tax_calculation(
            object(),
            company_id=company_id,
            tax_calculation_id=tax_calculation_id,
            adjustment_date=D1,
            created_by=created_by,
        )


@pytest.mark.asyncio
async def test_lifecycle_validates_adjustment_date():
    with pytest.raises(
        PurchaseReturnInputVatCreditCorrectionLifecycleError,
        match="adjustment_date must be a date",
    ):
        await reconcile_purchase_return_input_vat_credit_correction_lifecycle_for_tax_calculation(
            object(),
            company_id=1,
            tax_calculation_id=20,
            adjustment_date="2026-09-05",
            created_by=9,
        )


def test_public_signature_is_tax_calculation_wide():
    signature = inspect.signature(
        reconcile_purchase_return_input_vat_credit_correction_lifecycle_for_tax_calculation
    )

    assert tuple(
        signature.parameters
    ) == (
        "db",
        "company_id",
        "tax_calculation_id",
        "adjustment_date",
        "created_by",
    )


def test_lifecycle_has_no_forbidden_runtime_coupling():
    source = inspect.getsource(
        service
    )

    tree = ast.parse(
        source
    )

    imported_modules = {
        node.module
        for node in ast.walk(
            tree
        )
        if isinstance(
            node,
            ast.ImportFrom,
        )
        and node.module is not None
    }

    forbidden_module_fragments = (
        "supplier_advance",
        "tax_credit_evidence",
        "tax_recognition",
        "input_vat_fulfillment_bridge",
    )

    for fragment in forbidden_module_fragments:
        assert not any(
            fragment in module
            for module in imported_modules
        )


def test_lifecycle_does_not_own_transaction_boundary():
    source = inspect.getsource(
        service
    )

    tree = ast.parse(
        source
    )

    transaction_calls = {
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
            and node.func.attr
            in {
                "commit",
                "rollback",
            }
        )
    }

    assert transaction_calls == set()
