from datetime import date
from types import SimpleNamespace

import pytest

import app.api.v1.tax_invoices as api
from app.schemas.tax_invoice import OutputTaxInvoiceCreate
from app.services.tax_invoice_registration_lifecycle_service import (
    TaxInvoiceRegistrationError,
)


class FakeDb:
    def __init__(self):
        self.commit_calls = 0
        self.rollback_calls = 0
        self.scalar_result = None

    async def commit(self):
        self.commit_calls += 1

    async def rollback(self):
        self.rollback_calls += 1

    async def scalar(self, _statement):
        return self.scalar_result


@pytest.mark.asyncio
async def test_output_invoice_and_prepared_event_commit_once(
    monkeypatch,
):
    db = FakeDb()

    invoice = SimpleNamespace(
        id=41,
        document_date=date(2026, 9, 16),
    )

    calls = []

    async def fake_create(
        _db,
        **kwargs,
    ):
        calls.append(
            ("invoice", kwargs)
        )
        return invoice

    async def fake_registration(
        _db,
        **kwargs,
    ):
        calls.append(
            ("registration", kwargs)
        )
        return SimpleNamespace(id=51)

    monkeypatch.setattr(
        api,
        "create_output_tax_invoice",
        fake_create,
    )

    monkeypatch.setattr(
        api,
        "append_tax_invoice_registration_event",
        fake_registration,
    )

    payload = OutputTaxInvoiceCreate(
        source_kind="settlement",
        source_id=10,
        document_number="PN-1",
    )

    result = await api.create_output(
        company_id=7,
        payload=payload,
        db=db,
        user=SimpleNamespace(id=3),
        _=None,
    )

    assert result is invoice
    assert db.commit_calls == 1
    assert db.rollback_calls == 0

    assert [
        item[0]
        for item in calls
    ] == [
        "invoice",
        "registration",
    ]

    assert (
        calls[1][1]["status"]
        == "prepared"
    )


@pytest.mark.asyncio
async def test_registration_failure_rolls_back_whole_output_request(
    monkeypatch,
):
    db = FakeDb()

    async def fake_create(
        _db,
        **_kwargs,
    ):
        return SimpleNamespace(
            id=41,
            document_date=date(2026, 9, 16),
        )

    async def fake_registration(
        _db,
        **_kwargs,
    ):
        raise TaxInvoiceRegistrationError(
            "synthetic registration failure"
        )

    monkeypatch.setattr(
        api,
        "create_output_tax_invoice",
        fake_create,
    )

    monkeypatch.setattr(
        api,
        "append_tax_invoice_registration_event",
        fake_registration,
    )

    payload = OutputTaxInvoiceCreate(
        source_kind="fulfillment",
        source_id=10,
        document_number="PN-2",
    )

    with pytest.raises(
        Exception
    ) as exc_info:
        await api.create_output(
            company_id=7,
            payload=payload,
            db=db,
            user=SimpleNamespace(id=3),
            _=None,
        )

    assert (
        getattr(
            exc_info.value,
            "status_code",
            None,
        )
        == 409
    )

    assert db.commit_calls == 0
    assert db.rollback_calls == 1


@pytest.mark.asyncio
async def test_existing_registration_does_not_append_duplicate(
    monkeypatch,
):
    db = FakeDb()
    db.scalar_result = SimpleNamespace(
        id=77
    )

    invoice = SimpleNamespace(
        id=41,
        document_date=date(2026, 9, 16),
    )

    append_calls = 0

    async def fake_create(
        _db,
        **_kwargs,
    ):
        return invoice

    async def fake_registration(
        _db,
        **_kwargs,
    ):
        nonlocal append_calls
        append_calls += 1

    monkeypatch.setattr(
        api,
        "create_output_tax_invoice",
        fake_create,
    )

    monkeypatch.setattr(
        api,
        "append_tax_invoice_registration_event",
        fake_registration,
    )

    payload = OutputTaxInvoiceCreate(
        source_kind="order_advance",
        source_id=10,
        document_number="PN-3",
    )

    result = await api.create_output(
        company_id=7,
        payload=payload,
        db=db,
        user=SimpleNamespace(id=3),
        _=None,
    )

    assert result is invoice
    assert append_calls == 0
    assert db.commit_calls == 1
    assert db.rollback_calls == 0
