import asyncio
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.services.tax_credit_evidence_lifecycle_service as service

from app.services.input_tax_recognition_reconciliation_service import (
    InputTaxRecognitionReconciliationStateError,
)
from app.services.tax_credit_evidence_types import (
    TaxCreditEvidenceType,
)


D1 = date(
    2026,
    8,
    10,
)

D2 = date(
    2026,
    8,
    20,
)


@pytest.fixture(
    autouse=True,
)
def _stub_input_tax_gl_posting(
    monkeypatch,
):
    """
    Existing evidence lifecycle unit tests remain focused on
    evidence persistence / recognition behavior.

    Dedicated tests below verify the new GL orchestration.
    """
    monkeypatch.setattr(
        service,
        "post_created_input_vat_recognition_events",
        AsyncMock(),
    )


def evidence(
    *,
    evidence_id=1,
    tax_calculation_id=77,
):
    return SimpleNamespace(
        id=evidence_id,
        company_id=1,
        tax_calculation_id=(
            tax_calculation_id
        ),
    )


def create_kwargs():
    return {
        "company_id": 1,
        "tax_calculation_id": 77,
        "evidence_type": (
            TaxCreditEvidenceType
            .REGISTERED_TAX_INVOICE
        ),
        "evidence_number": "PN-77",
        "evidence_date": D1,
        "credit_available_date": D1,
        "evidenced_taxable_base": (
            Decimal("100.00")
        ),
        "evidenced_tax_amount": (
            Decimal("20.00")
        ),
        "currency_code": "UAH",
        "adjustment_date": D2,
        "created_by": 9,
    }


def test_create_persists_before_input_reconciliation(
    monkeypatch,
):
    calls = []

    persisted = evidence()

    expected_recognition = object()

    async def fake_create(
        db,
        **kwargs,
    ):
        calls.append(
            (
                "persist",
                kwargs,
            )
        )

        return persisted

    async def fake_reconcile(
        db,
        **kwargs,
    ):
        calls.append(
            (
                "reconcile",
                kwargs,
            )
        )

        return expected_recognition

    monkeypatch.setattr(
        service,
        "create_tax_credit_evidence",
        fake_create,
    )

    monkeypatch.setattr(
        service,
        "reconcile_input_tax_calculation_from_active_sources",
        fake_reconcile,
    )

    result = asyncio.run(
        service.create_tax_credit_evidence_and_reconcile(
            object(),
            **create_kwargs(),
        )
    )

    assert (
        result.evidence
        is persisted
    )

    assert (
        result.recognition
        is expected_recognition
    )

    assert [
        item[0]
        for item in calls
    ] == [
        "persist",
        "reconcile",
    ]

    reconcile_kwargs = (
        calls[1][1]
    )

    assert (
        reconcile_kwargs[
            "company_id"
        ]
        == 1
    )

    assert (
        reconcile_kwargs[
            "tax_calculation_id"
        ]
        == 77
    )

    assert (
        reconcile_kwargs[
            "adjustment_date"
        ]
        == D2
    )

    assert (
        reconcile_kwargs[
            "created_by"
        ]
        == 9
    )


def test_reverse_persists_before_input_reconciliation(
    monkeypatch,
):
    calls = []

    reversal = evidence(
        evidence_id=2,
    )

    expected_recognition = object()

    async def fake_reverse(
        db,
        **kwargs,
    ):
        calls.append(
            (
                "persist_reverse",
                kwargs,
            )
        )

        return reversal

    async def fake_reconcile(
        db,
        **kwargs,
    ):
        calls.append(
            (
                "reconcile",
                kwargs,
            )
        )

        return expected_recognition

    monkeypatch.setattr(
        service,
        "reverse_tax_credit_evidence",
        fake_reverse,
    )

    monkeypatch.setattr(
        service,
        "reconcile_input_tax_calculation_from_active_sources",
        fake_reconcile,
    )

    result = asyncio.run(
        service.reverse_tax_credit_evidence_and_reconcile(
            object(),
            company_id=1,
            evidence_id=10,
            reversal_date=D2,
            reversed_by=11,
        )
    )

    assert (
        result.evidence
        is reversal
    )

    assert (
        result.recognition
        is expected_recognition
    )

    assert [
        item[0]
        for item in calls
    ] == [
        "persist_reverse",
        "reconcile",
    ]

    reconcile_kwargs = (
        calls[1][1]
    )

    assert (
        reconcile_kwargs[
            "tax_calculation_id"
        ]
        == 77
    )

    assert (
        reconcile_kwargs[
            "adjustment_date"
        ]
        == D2
    )

    assert (
        reconcile_kwargs[
            "created_by"
        ]
        == 11
    )


def test_create_reconciliation_failure_is_wrapped(
    monkeypatch,
):
    monkeypatch.setattr(
        service,
        "create_tax_credit_evidence",
        AsyncMock(
            return_value=evidence()
        ),
    )

    monkeypatch.setattr(
        service,
        "reconcile_input_tax_calculation_from_active_sources",
        AsyncMock(
            side_effect=(
                InputTaxRecognitionReconciliationStateError(
                    "broken INPUT recognition"
                )
            )
        ),
    )

    with pytest.raises(
        service.TaxCreditEvidenceLifecycleError,
        match=(
            "failed after "
            "TaxCreditEvidence mutation"
        ),
    ):
        asyncio.run(
            service.create_tax_credit_evidence_and_reconcile(
                object(),
                **create_kwargs(),
            )
        )


def test_reverse_reconciliation_failure_is_wrapped(
    monkeypatch,
):
    monkeypatch.setattr(
        service,
        "reverse_tax_credit_evidence",
        AsyncMock(
            return_value=evidence(
                evidence_id=2
            )
        ),
    )

    monkeypatch.setattr(
        service,
        "reconcile_input_tax_calculation_from_active_sources",
        AsyncMock(
            side_effect=(
                InputTaxRecognitionReconciliationStateError(
                    "broken INPUT recognition"
                )
            )
        ),
    )

    with pytest.raises(
        service.TaxCreditEvidenceLifecycleError,
        match=(
            "failed after "
            "TaxCreditEvidence mutation"
        ),
    ):
        asyncio.run(
            service.reverse_tax_credit_evidence_and_reconcile(
                object(),
                company_id=1,
                evidence_id=10,
                reversal_date=D2,
                reversed_by=11,
            )
        )


def test_invalid_create_adjustment_date_fails_before_persistence(
    monkeypatch,
):
    persist = AsyncMock()

    monkeypatch.setattr(
        service,
        "create_tax_credit_evidence",
        persist,
    )

    kwargs = create_kwargs()

    kwargs[
        "adjustment_date"
    ] = "2026-08-20"

    with pytest.raises(
        ValueError,
        match=(
            "adjustment_date must be a date"
        ),
    ):
        asyncio.run(
            service.create_tax_credit_evidence_and_reconcile(
                object(),
                **kwargs,
            )
        )

    persist.assert_not_awaited()


def test_lifecycle_service_does_not_commit_or_rollback():
    import inspect

    source = inspect.getsource(
        service
    )

    assert "db.commit(" not in source
    assert "db.rollback(" not in source


def test_create_orders_persistence_reconciliation_then_gl(
    monkeypatch,
):
    calls = []

    persisted = evidence()
    recognition = SimpleNamespace(
        created_events=()
    )

    async def fake_create(
        db,
        **kwargs,
    ):
        calls.append(
            "persist"
        )
        return persisted

    async def fake_reconcile(
        db,
        **kwargs,
    ):
        calls.append(
            "reconcile"
        )
        return recognition

    async def fake_gl(
        db,
        **kwargs,
    ):
        calls.append(
            "gl"
        )
        assert (
            kwargs["result"]
            is recognition
        )
        assert (
            kwargs["created_by"]
            == 9
        )

    monkeypatch.setattr(
        service,
        "create_tax_credit_evidence",
        fake_create,
    )
    monkeypatch.setattr(
        service,
        "reconcile_input_tax_calculation_from_active_sources",
        fake_reconcile,
    )
    monkeypatch.setattr(
        service,
        "post_created_input_vat_recognition_events",
        fake_gl,
    )

    result = asyncio.run(
        service.create_tax_credit_evidence_and_reconcile(
            object(),
            **create_kwargs(),
        )
    )

    assert (
        result.evidence
        is persisted
    )
    assert (
        result.recognition
        is recognition
    )
    assert calls == [
        "persist",
        "reconcile",
        "gl",
    ]


def test_reverse_orders_persistence_reconciliation_then_gl(
    monkeypatch,
):
    calls = []

    reversal = evidence(
        evidence_id=2
    )
    recognition = SimpleNamespace(
        created_events=()
    )

    async def fake_reverse(
        db,
        **kwargs,
    ):
        calls.append(
            "persist_reverse"
        )
        return reversal

    async def fake_reconcile(
        db,
        **kwargs,
    ):
        calls.append(
            "reconcile"
        )
        return recognition

    async def fake_gl(
        db,
        **kwargs,
    ):
        calls.append(
            "gl"
        )
        assert (
            kwargs["result"]
            is recognition
        )
        assert (
            kwargs["created_by"]
            == 11
        )

    monkeypatch.setattr(
        service,
        "reverse_tax_credit_evidence",
        fake_reverse,
    )
    monkeypatch.setattr(
        service,
        "reconcile_input_tax_calculation_from_active_sources",
        fake_reconcile,
    )
    monkeypatch.setattr(
        service,
        "post_created_input_vat_recognition_events",
        fake_gl,
    )

    result = asyncio.run(
        service.reverse_tax_credit_evidence_and_reconcile(
            object(),
            company_id=1,
            evidence_id=10,
            reversal_date=D2,
            reversed_by=11,
        )
    )

    assert (
        result.evidence
        is reversal
    )
    assert (
        result.recognition
        is recognition
    )
    assert calls == [
        "persist_reverse",
        "reconcile",
        "gl",
    ]


def test_evidence_lifecycle_wraps_input_gl_failure(
    monkeypatch,
):
    persisted = evidence()

    recognition = SimpleNamespace(
        created_events=()
    )

    monkeypatch.setattr(
        service,
        "create_tax_credit_evidence",
        AsyncMock(
            return_value=persisted
        ),
    )
    monkeypatch.setattr(
        service,
        "reconcile_input_tax_calculation_from_active_sources",
        AsyncMock(
            return_value=recognition
        ),
    )
    monkeypatch.setattr(
        service,
        "post_created_input_vat_recognition_events",
        AsyncMock(
            side_effect=(
                service.TaxRecognitionJournalError(
                    "GL failed"
                )
            )
        ),
    )

    with pytest.raises(
        service.TaxCreditEvidenceLifecycleError,
        match=(
            "journal posting failed after "
            "TaxCreditEvidence mutation"
        ),
    ):
        asyncio.run(
            service.create_tax_credit_evidence_and_reconcile(
                object(),
                **create_kwargs(),
            )
        )
