import ast
import inspect
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

import app.services.purchase_return_input_vat_credit_correction_reconciliation_service as service
from app.models.purchase_return_input_vat_credit_correction_event import (
    PurchaseReturnInputVatCreditCorrectionEvent,
)
from app.models.purchase_return_vat_adjustment_event import (
    PurchaseReturnVatAdjustmentEvent,
)
from app.models.tax_recognition_event import (
    TaxRecognitionEvent,
)
from app.services.purchase_return_input_vat_credit_correction_reconciliation_service import (
    PurchaseReturnInputVatCreditCorrectionReconciliationIntegrityError,
    PurchaseReturnInputVatCreditCorrectionReconciliationStateError,
    _active_correction_map,
    _active_return_events,
    _formed_input_credit,
    _validate_forward_chronology,
    reconcile_purchase_return_input_vat_credit_corrections_for_tax_calculation,
)
from app.services.tax_types import (
    TaxDirection,
    TaxType,
)


D1 = date(
    2026,
    9,
    1,
)

D5 = date(
    2026,
    9,
    5,
)

D10 = date(
    2026,
    9,
    10,
)


def calculation():
    return SimpleNamespace(
        id=20,
        company_id=1,
        tax_type=TaxType.VAT,
        direction=TaxDirection.INPUT,
        taxable_base=Decimal("100.00"),
        tax_amount=Decimal("20.00"),
        currency_code="UAH",
        calculation_date=D1,
    )


def recognition(
    event_id,
    *,
    base="100.00",
    tax="20.00",
    recognition_date=D1,
    evidence_id=1000,
    reversal_of_id=None,
):
    return TaxRecognitionEvent(
        id=event_id,
        company_id=1,
        tax_calculation_id=20,
        invoice_fulfillment_allocation_id=None,
        payment_settlement_allocation_id=None,
        tax_credit_evidence_id=evidence_id,
        recognition_date=recognition_date,
        recognized_taxable_base=Decimal(
            base
        ),
        recognized_tax_amount=Decimal(
            tax
        ),
        currency_code="UAH",
        created_by=7,
        reversal_of_id=reversal_of_id,
    )


def vat_return(
    event_id,
    *,
    prre_id,
    base="25.00",
    tax="5.00",
    adjustment_date=D5,
    basis_kind="goods_received_by_supplier",
    reversal_of_id=None,
):
    return (
        PurchaseReturnVatAdjustmentEvent(
            id=event_id,
            company_id=1,
            purchase_return_recognition_event_id=(
                prre_id
            ),
            tax_calculation_id=20,
            adjustment_date=(
                adjustment_date
            ),
            basis_kind=(
                basis_kind
            ),
            adjusted_taxable_base=Decimal(
                base
            ),
            adjusted_tax_amount=Decimal(
                tax
            ),
            currency_code="UAH",
            created_by=7,
            reversal_of_id=reversal_of_id,
        )
    )


def correction(
    event_id,
    *,
    source_id,
    base="15.00",
    tax="3.00",
    adjustment_date=D5,
    reversal_of_id=None,
):
    return (
        PurchaseReturnInputVatCreditCorrectionEvent(
            id=event_id,
            company_id=1,
            purchase_return_vat_adjustment_event_id=(
                source_id
            ),
            tax_calculation_id=20,
            adjustment_date=(
                adjustment_date
            ),
            reduced_taxable_base=Decimal(
                base
            ),
            reduced_tax_amount=Decimal(
                tax
            ),
            currency_code="UAH",
            created_by=7,
            reversal_of_id=reversal_of_id,
        )
    )


def test_full_history_resolves_backdated_replacement_before_balance():
    original = recognition(
        1,
        base="100",
        tax="20",
        recognition_date=D1,
    )

    reversal = recognition(
        2,
        base="100",
        tax="20",
        recognition_date=D5,
        reversal_of_id=1,
    )

    replacement = recognition(
        3,
        base="75",
        tax="15",
        recognition_date=D1,
    )

    assert _formed_input_credit(
        (
            original,
            reversal,
            replacement,
        )
    ) == (
        Decimal("75"),
        Decimal("15"),
    )


def test_forward_chronology_rejects_historical_replay():
    with pytest.raises(
        PurchaseReturnInputVatCreditCorrectionReconciliationStateError
    ):
        _validate_forward_chronology(
            adjustment_date=D1,
            calculation_date=D1,
            recognition_events=(
                recognition(
                    1,
                    recognition_date=D5,
                ),
            ),
            return_events=(),
            correction_events=(),
        )


def test_basis_replacement_leaves_only_new_active_return():
    original = vat_return(
        10,
        prre_id=30,
    )

    reversal = vat_return(
        11,
        prre_id=30,
        adjustment_date=D10,
        reversal_of_id=10,
    )

    replacement = vat_return(
        12,
        prre_id=30,
        adjustment_date=D10,
        basis_kind="refund_by_supplier",
    )

    active = _active_return_events(
        (
            original,
            reversal,
            replacement,
        ),
        company_id=1,
        tax_calculation_id=20,
        currency_code="UAH",
    )

    assert tuple(
        event.id
        for event in active
    ) == (
        12,
    )


def test_multiple_active_basis_states_for_same_prre_fail():
    with pytest.raises(
        PurchaseReturnInputVatCreditCorrectionReconciliationIntegrityError
    ):
        _active_return_events(
            (
                vat_return(
                    10,
                    prre_id=30,
                ),
                vat_return(
                    11,
                    prre_id=30,
                    basis_kind="refund_by_supplier",
                ),
            ),
            company_id=1,
            tax_calculation_id=20,
            currency_code="UAH",
        )


def test_correction_source_cannot_point_to_vat_reversal():
    original = vat_return(
        10,
        prre_id=30,
    )

    reversal = vat_return(
        11,
        prre_id=30,
        adjustment_date=D10,
        reversal_of_id=10,
    )

    with pytest.raises(
        PurchaseReturnInputVatCreditCorrectionReconciliationIntegrityError
    ):
        _active_correction_map(
            (
                correction(
                    100,
                    source_id=11,
                    adjustment_date=D10,
                ),
            ),
            company_id=1,
            tax_calculation_id=20,
            currency_code="UAH",
            return_events_by_id={
                10: original,
                11: reversal,
            },
        )


async def _patch_state(
    monkeypatch,
    *,
    recognition_history,
    return_history,
    correction_history=(),
    persist_calls=None,
):
    async def load_calculation(
        *args,
        **kwargs,
    ):
        return calculation()

    async def load_recognition(
        *args,
        **kwargs,
    ):
        return tuple(
            recognition_history
        )

    async def load_returns(
        *args,
        **kwargs,
    ):
        return tuple(
            return_history
        )

    async def load_corrections(
        *args,
        **kwargs,
    ):
        return tuple(
            correction_history
        )

    monkeypatch.setattr(
        service,
        "_load_locked_calculation",
        load_calculation,
    )

    monkeypatch.setattr(
        service,
        "_load_locked_recognition_history",
        load_recognition,
    )

    monkeypatch.setattr(
        service,
        "_load_locked_return_history",
        load_returns,
    )

    monkeypatch.setattr(
        service,
        "_load_locked_correction_history",
        load_corrections,
    )

    if persist_calls is not None:
        async def fake_persist(
            db,
            *,
            company_id,
            target,
            created_by,
            reversal_date=None,
        ):
            persist_calls.append(
                (
                    target,
                    reversal_date,
                )
            )

            return ()

        monkeypatch.setattr(
            service,
            "reconcile_purchase_return_input_vat_credit_correction_source",
            fake_persist,
        )


@pytest.mark.asyncio
async def test_no_credit_formed_produces_zero_desired_correction(
    monkeypatch,
):
    calls = []

    await _patch_state(
        monkeypatch,
        recognition_history=(),
        return_history=(
            vat_return(
                10,
                prre_id=30,
            ),
        ),
        persist_calls=calls,
    )

    result = (
        await reconcile_purchase_return_input_vat_credit_corrections_for_tax_calculation(
            object(),
            company_id=1,
            tax_calculation_id=20,
            adjustment_date=D5,
            created_by=9,
        )
    )

    assert (
        result.formed_credit_tax_amount
        == Decimal("0")
    )

    assert len(
        result.desired_targets
    ) == 1

    assert (
        result.desired_targets[
            0
        ].reduced_tax_amount
        == Decimal("0")
    )


@pytest.mark.asyncio
async def test_partial_credit_two_returns_allocates_three_then_five(
    monkeypatch,
):
    calls = []

    await _patch_state(
        monkeypatch,
        recognition_history=(
            recognition(
                1,
                base="90",
                tax="18",
            ),
        ),
        return_history=(
            vat_return(
                10,
                prre_id=30,
                adjustment_date=D5,
            ),
            vat_return(
                20,
                prre_id=40,
                adjustment_date=D10,
            ),
        ),
        persist_calls=calls,
    )

    result = (
        await reconcile_purchase_return_input_vat_credit_corrections_for_tax_calculation(
            object(),
            company_id=1,
            tax_calculation_id=20,
            adjustment_date=D10,
            created_by=9,
        )
    )

    assert tuple(
        target.reduced_tax_amount
        for target
        in result.desired_targets
    ) == (
        Decimal("3"),
        Decimal("5"),
    )


@pytest.mark.asyncio
async def test_late_full_credit_creates_later_five_target(
    monkeypatch,
):
    calls = []

    await _patch_state(
        monkeypatch,
        recognition_history=(
            recognition(
                1,
                recognition_date=D10,
            ),
        ),
        return_history=(
            vat_return(
                10,
                prre_id=30,
                adjustment_date=D5,
            ),
        ),
        persist_calls=calls,
    )

    result = (
        await reconcile_purchase_return_input_vat_credit_corrections_for_tax_calculation(
            object(),
            company_id=1,
            tax_calculation_id=20,
            adjustment_date=D10,
            created_by=9,
        )
    )

    target = result.desired_targets[
        0
    ]

    assert (
        target.reduced_tax_amount
        == Decimal("5")
    )

    assert (
        target.adjustment_date
        == D10
    )


@pytest.mark.asyncio
async def test_reversed_earlier_return_zeroes_old_source_then_shifts_peer(
    monkeypatch,
):
    calls = []

    first = vat_return(
        10,
        prre_id=30,
        adjustment_date=D5,
    )

    first_reversal = vat_return(
        11,
        prre_id=30,
        adjustment_date=D10,
        reversal_of_id=10,
    )

    second = vat_return(
        20,
        prre_id=40,
        adjustment_date=D5,
    )

    await _patch_state(
        monkeypatch,
        recognition_history=(
            recognition(
                1,
                base="90",
                tax="18",
            ),
        ),
        return_history=(
            first,
            first_reversal,
            second,
        ),
        correction_history=(
            correction(
                100,
                source_id=10,
                base="15",
                tax="3",
                adjustment_date=D5,
            ),
            correction(
                200,
                source_id=20,
                base="25",
                tax="5",
                adjustment_date=D5,
            ),
        ),
        persist_calls=calls,
    )

    result = (
        await reconcile_purchase_return_input_vat_credit_corrections_for_tax_calculation(
            object(),
            company_id=1,
            tax_calculation_id=20,
            adjustment_date=D10,
            created_by=9,
        )
    )

    assert (
        result.zeroed_correction_source_ids
        == (
            10,
        )
    )

    assert [
        item[
            0
        ].purchase_return_vat_adjustment_event_id
        for item in calls
    ] == [
        10,
        20,
    ]

    assert (
        calls[
            0
        ][
            0
        ].is_zero
        is True
    )

    assert (
        calls[
            1
        ][
            0
        ].reduced_tax_amount
        == Decimal("3")
    )


@pytest.mark.asyncio
async def test_same_amount_on_later_reconciliation_preserves_original_date(
    monkeypatch,
):
    calls = []

    await _patch_state(
        monkeypatch,
        recognition_history=(
            recognition(
                1,
            ),
        ),
        return_history=(
            vat_return(
                10,
                prre_id=30,
                adjustment_date=D5,
            ),
        ),
        correction_history=(
            correction(
                100,
                source_id=10,
                base="25",
                tax="5",
                adjustment_date=D5,
            ),
        ),
        persist_calls=calls,
    )

    result = (
        await reconcile_purchase_return_input_vat_credit_corrections_for_tax_calculation(
            object(),
            company_id=1,
            tax_calculation_id=20,
            adjustment_date=D10,
            created_by=9,
        )
    )

    assert (
        result.desired_targets[
            0
        ].adjustment_date
        == D5
    )

    assert (
        calls[
            0
        ][
            0
        ].adjustment_date
        == D5
    )


@pytest.mark.asyncio
async def test_result_reports_active_sources_and_current_corrections(
    monkeypatch,
):
    calls = []

    await _patch_state(
        monkeypatch,
        recognition_history=(
            recognition(
                1
            ),
        ),
        return_history=(
            vat_return(
                10,
                prre_id=30,
            ),
        ),
        correction_history=(
            correction(
                100,
                source_id=10,
                base="25",
                tax="5",
            ),
        ),
        persist_calls=calls,
    )

    result = (
        await reconcile_purchase_return_input_vat_credit_corrections_for_tax_calculation(
            object(),
            company_id=1,
            tax_calculation_id=20,
            adjustment_date=D5,
            created_by=9,
        )
    )

    assert (
        result.active_return_event_ids
        == (
            10,
        )
    )

    assert (
        result.current_correction_source_ids
        == (
            10,
        )
    )

    assert (
        result.zeroed_correction_source_ids
        == ()
    )


def test_service_contains_no_forbidden_runtime_coupling():
    source = inspect.getsource(
        service
    )

    tree = ast.parse(
        source
    )

    runtime_names = {
        node.id
        for node in ast.walk(
            tree
        )
        if isinstance(
            node,
            ast.Name,
        )
    }

    runtime_attributes = {
        node.attr
        for node in ast.walk(
            tree
        )
        if isinstance(
            node,
            ast.Attribute,
        )
    }

    imported_names = set()

    for node in ast.walk(
        tree
    ):
        if isinstance(
            node,
            ast.ImportFrom,
        ):
            for alias in node.names:
                imported_names.add(
                    alias.asname
                    or alias.name
                )

        elif isinstance(
            node,
            ast.Import,
        ):
            for alias in node.names:
                imported_names.add(
                    alias.asname
                    or alias.name.split(
                        "."
                    )[
                        0
                    ]
                )

    referenced = (
        runtime_names
        | runtime_attributes
        | imported_names
    )

    forbidden = {
        "JournalEntry",
        "TaxCreditEvidence",
    }

    assert not (
        forbidden
        & referenced
    )

    assert not any(
        name.startswith(
            "reconcile_supplier_advance"
        )
        for name in referenced
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


def test_created_at_is_not_used_for_balance_reconstruction():
    source = inspect.getsource(
        service
    )

    assert ".created_at" not in source
    assert "created_at <=" not in source
