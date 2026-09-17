from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

import app.api.v1.tax_invoice_corrections as api
import app.services.tax_invoice_correction_orchestration_service as orchestration

from app.schemas.tax_invoice_correction import (
    InputTaxInvoiceCorrectionCreate,
    OutputTaxInvoiceCorrectionCreate,
)
from app.services.idempotency_decision_types import (
    IdempotencyDecision,
)
from app.services.tax_invoice_correction_orchestration_service import (
    TaxInvoiceCorrectionIdempotencyConflictError,
    _canonical_create_payload,
    create_tax_invoice_correction_idempotent,
)
from app.services.tax_invoice_correction_source_resolver_service import (
    TaxInvoiceCorrectionLineSource,
)


D1 = date(2026, 9, 16)
D2 = date(2026, 9, 17)


class FakeDb:
    def __init__(self):
        self.commit_calls = 0
        self.rollback_calls = 0

    async def commit(self):
        self.commit_calls += 1

    async def rollback(self):
        self.rollback_calls += 1


def test_create_fingerprint_payload_orders_lines():
    sources = (
        TaxInvoiceCorrectionLineSource(
            line_number=1,
            source_kind="sales_return",
            source_id=10,
            reason_code="return",
        ),
        TaxInvoiceCorrectionLineSource(
            line_number=2,
            source_kind="recognition_reversal",
            source_id=20,
            reason_code="refund",
        ),
    )

    payload = _canonical_create_payload(
        direction="output",
        original_tax_invoice_id=7,
        document_number="RK-1",
        document_date=D1,
        sources=sources,
        registration_date=None,
        registration_reference=None,
    )

    assert [
        item["line_number"]
        for item in payload["lines"]
    ] == [1, 2]

    assert (
        payload["request_key"]
        if "request_key" in payload
        else None
    ) is None


@pytest.mark.asyncio
async def test_output_orchestration_is_one_atomic_logical_write(
    monkeypatch,
):
    db = object()

    reserve = AsyncMock(
        return_value=SimpleNamespace(
            decision=IdempotencyDecision.START_NEW,
            reusable_result=None,
        )
    )

    correction = SimpleNamespace(
        id=41,
        direction="output",
    )

    create = AsyncMock(
        return_value=correction
    )

    append = AsyncMock(
        return_value=SimpleNamespace(
            id=51
        )
    )

    complete = AsyncMock()

    monkeypatch.setattr(
        orchestration,
        "reserve_idempotent_request",
        reserve,
    )
    monkeypatch.setattr(
        orchestration,
        "create_tax_invoice_correction",
        create,
    )
    monkeypatch.setattr(
        orchestration,
        "append_tax_invoice_correction_registration_event",
        append,
    )
    monkeypatch.setattr(
        orchestration,
        "complete_idempotent_operation",
        complete,
    )

    result = await create_tax_invoice_correction_idempotent(
        db,
        company_id=1,
        request_key="rk-output-1",
        direction="output",
        original_tax_invoice_id=7,
        document_number="RK-1",
        document_date=D1,
        sources=[
            TaxInvoiceCorrectionLineSource(
                line_number=1,
                source_kind="recognition_reversal",
                source_id=20,
                reason_code="refund",
            )
        ],
        created_by=3,
    )

    assert result is correction

    reserve.assert_awaited_once()
    create.assert_awaited_once()
    append.assert_awaited_once()
    complete.assert_awaited_once()

    assert (
        append.await_args.kwargs["status"]
        == "prepared"
    )

    assert (
        complete.await_args.kwargs["result_id"]
        == "41"
    )


@pytest.mark.asyncio
async def test_input_orchestration_initial_state_is_registered(
    monkeypatch,
):
    db = object()

    monkeypatch.setattr(
        orchestration,
        "reserve_idempotent_request",
        AsyncMock(
            return_value=SimpleNamespace(
                decision=IdempotencyDecision.START_NEW,
                reusable_result=None,
            )
        ),
    )

    monkeypatch.setattr(
        orchestration,
        "create_tax_invoice_correction",
        AsyncMock(
            return_value=SimpleNamespace(
                id=91,
                direction="input",
            )
        ),
    )

    append = AsyncMock(
        return_value=SimpleNamespace(
            id=92
        )
    )

    monkeypatch.setattr(
        orchestration,
        "append_tax_invoice_correction_registration_event",
        append,
    )

    monkeypatch.setattr(
        orchestration,
        "complete_idempotent_operation",
        AsyncMock(),
    )

    await create_tax_invoice_correction_idempotent(
        db,
        company_id=1,
        request_key="rk-input-1",
        direction="input",
        original_tax_invoice_id=8,
        document_number="RK-IN-1",
        document_date=D1,
        registration_date=D2,
        registration_reference="receipt-1",
        sources=[
            TaxInvoiceCorrectionLineSource(
                line_number=1,
                source_kind="purchase_return",
                source_id=30,
                reason_code="return",
            )
        ],
        created_by=3,
    )

    assert (
        append.await_args.kwargs["status"]
        == "registered"
    )

    assert (
        append.await_args.kwargs["event_date"]
        == D2
    )

    assert (
        append.await_args.kwargs["reference"]
        == "receipt-1"
    )


@pytest.mark.asyncio
async def test_reuse_result_skips_business_create(
    monkeypatch,
):
    db = object()

    monkeypatch.setattr(
        orchestration,
        "reserve_idempotent_request",
        AsyncMock(
            return_value=SimpleNamespace(
                decision=IdempotencyDecision.REUSE_RESULT,
                reusable_result=SimpleNamespace(
                    result_type="tax_invoice_correction",
                    result_id="44",
                ),
            )
        ),
    )

    reusable = SimpleNamespace(
        id=44,
        direction="output",
    )

    loader = AsyncMock(
        return_value=reusable
    )

    monkeypatch.setattr(
        orchestration,
        "_reusable_correction",
        loader,
    )

    create = AsyncMock()

    monkeypatch.setattr(
        orchestration,
        "create_tax_invoice_correction",
        create,
    )

    result = await create_tax_invoice_correction_idempotent(
        db,
        company_id=1,
        request_key="repeat",
        direction="output",
        original_tax_invoice_id=7,
        document_number="RK-1",
        document_date=D1,
        sources=[
            TaxInvoiceCorrectionLineSource(
                line_number=1,
                source_kind="recognition_reversal",
                source_id=20,
                reason_code="refund",
            )
        ],
        created_by=3,
    )

    assert result is reusable
    loader.assert_awaited_once()
    create.assert_not_awaited()


@pytest.mark.asyncio
async def test_idempotency_key_fingerprint_conflict_is_wrapped(
    monkeypatch,
):
    async def fail_reservation(**_kwargs):
        from app.services.idempotency_request_validator import (
            IdempotencyKeyReuseError,
        )

        raise IdempotencyKeyReuseError(
            "different request"
        )

    monkeypatch.setattr(
        orchestration,
        "reserve_idempotent_request",
        fail_reservation,
    )

    with pytest.raises(
        TaxInvoiceCorrectionIdempotencyConflictError
    ):
        await create_tax_invoice_correction_idempotent(
            object(),
            company_id=1,
            request_key="same-key",
            direction="output",
            original_tax_invoice_id=7,
            document_number="RK-1",
            document_date=D1,
            sources=[
                TaxInvoiceCorrectionLineSource(
                    line_number=1,
                    source_kind="recognition_reversal",
                    source_id=20,
                    reason_code="refund",
                )
            ],
            created_by=3,
        )


@pytest.mark.asyncio
async def test_api_commit_helper_commits_once():
    db = FakeDb()

    async def operation():
        return SimpleNamespace(
            id=123
        )

    result_id = await api._commit_id_or_409(
        db,
        operation(),
    )

    assert result_id == 123
    assert db.commit_calls == 1
    assert db.rollback_calls == 0


@pytest.mark.asyncio
async def test_api_commit_helper_rolls_back_domain_error():
    db = FakeDb()

    async def operation():
        raise (
            TaxInvoiceCorrectionIdempotencyConflictError(
                "conflict"
            )
        )

    with pytest.raises(
        HTTPException
    ) as exc_info:
        await api._commit_id_or_409(
            db,
            operation(),
        )

    assert exc_info.value.status_code == 409
    assert db.commit_calls == 0
    assert db.rollback_calls == 1


def test_output_and_input_request_key_contract():
    output = OutputTaxInvoiceCorrectionCreate(
        request_key="rk-out",
        original_tax_invoice_id=7,
        document_number="RK-OUT",
        document_date=D1,
        lines=[
            {
                "line_number": 1,
                "source_kind": "recognition_reversal",
                "source_id": 20,
                "reason_code": "refund",
            }
        ],
    )

    input_request = InputTaxInvoiceCorrectionCreate(
        request_key="rk-in",
        original_tax_invoice_id=8,
        document_number="RK-IN",
        document_date=D1,
        registered_on=D2,
        receipt_reference="receipt",
        lines=[
            {
                "line_number": 1,
                "source_kind": "purchase_return",
                "source_id": 30,
                "reason_code": "return",
            }
        ],
    )

    assert output.request_key == "rk-out"
    assert input_request.request_key == "rk-in"
