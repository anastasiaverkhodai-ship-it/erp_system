from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.invoice_fulfillment_allocation_service import (
    InvoiceFulfillmentAllocationError,
    InvoiceFulfillmentAllocationNotFoundError,
    InvoiceFulfillmentAllocationReversalStateError,
    has_active_fulfillment_allocations,
    has_active_invoice_allocations,
    reverse_invoice_fulfillment_allocation,
)
from app.services.invoice_fulfillment_allocation_types import (
    InvoiceFulfillmentAllocationStatus,
)


class ScalarResult:
    def __init__(
        self,
        value,
    ):
        self.value = value

    def scalar_one_or_none(
        self,
    ):
        return self.value


@pytest.fixture(autouse=True)
def _mock_locked_invoice_for_legacy_reversal_unit_tests(
    monkeypatch,
):
    """
    These pre-existing tests exercise allocation-reversal
    semantics, not TradeDocument loading.

    Reversal now locks the Invoice header first so its lock
    order matches allocation creation and supplier-clearing
    reconciliation.

    Keep the old unit tests focused on their original contract
    by isolating the new Invoice dependency here.

    Supplier/PURCHASE lifecycle routing is covered separately
    by test_supplier_advance_clearing_lifecycle_service.py.
    """

    monkeypatch.setitem(
        reverse_invoice_fulfillment_allocation.__globals__,
        "_get_locked_invoice",
        AsyncMock(
            return_value=(
                SimpleNamespace(
                    # Deliberately non-PURCHASE.
                    # Old reversal tests are not supplier
                    # lifecycle integration tests.
                    direction=object(),
                )
            )
        ),
    )


@pytest.mark.asyncio
async def test_reverse_active_allocation():
    allocation = SimpleNamespace(
        status=(
            InvoiceFulfillmentAllocationStatus.ACTIVE
        ),
        quantity=10,
        reversed_by=None,
        reversed_at=None,
    )
    allocation.invoice_id = 5
    allocation.invoice_line_id = 50

    db = SimpleNamespace(
        execute=AsyncMock(
            return_value=ScalarResult(
                allocation
            )
        ),
        flush=AsyncMock(),
    )

    result = (
        await reverse_invoice_fulfillment_allocation(
            db,
            company_id=1,
            invoice_id=5,
            allocation_id=10,
            reversed_by=7,
        )
    )

    assert result is allocation

    assert (
        allocation.status
        == InvoiceFulfillmentAllocationStatus.REVERSED
    )

    assert allocation.quantity == 10
    assert allocation.reversed_by == 7
    assert allocation.reversed_at is not None

    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_double_reversal_is_blocked():
    allocation = SimpleNamespace(
        status=(
            InvoiceFulfillmentAllocationStatus.REVERSED
        ),
        reversed_by=7,
        reversed_at=object(),
    )

    db = SimpleNamespace(
        execute=AsyncMock(
            return_value=ScalarResult(
                allocation
            )
        ),
        flush=AsyncMock(),
    )

    with pytest.raises(
        InvoiceFulfillmentAllocationReversalStateError
    ):
        await reverse_invoice_fulfillment_allocation(
            db,
            company_id=1,
            invoice_id=5,
            allocation_id=10,
            reversed_by=7,
        )

    db.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_reversal_missing_allocation():
    db = SimpleNamespace(
        execute=AsyncMock(
            return_value=ScalarResult(
                None
            )
        ),
        flush=AsyncMock(),
    )

    with pytest.raises(
        InvoiceFulfillmentAllocationNotFoundError
    ):
        await reverse_invoice_fulfillment_allocation(
            db,
            company_id=1,
            invoice_id=5,
            allocation_id=999,
            reversed_by=7,
        )


@pytest.mark.asyncio
async def test_reversal_requires_actor():
    db = SimpleNamespace(
        execute=AsyncMock(),
        flush=AsyncMock(),
    )

    with pytest.raises(
        InvoiceFulfillmentAllocationError
    ):
        await reverse_invoice_fulfillment_allocation(
            db,
            company_id=1,
            invoice_id=5,
            allocation_id=10,
            reversed_by=0,
        )

    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_active_invoice_guard_true():
    db = SimpleNamespace(
        execute=AsyncMock(
            return_value=ScalarResult(
                123
            )
        )
    )

    assert await has_active_invoice_allocations(
        db,
        company_id=1,
        invoice_id=2,
        lock_rows=True,
    )


@pytest.mark.asyncio
async def test_active_invoice_guard_false():
    db = SimpleNamespace(
        execute=AsyncMock(
            return_value=ScalarResult(
                None
            )
        )
    )

    assert not await has_active_invoice_allocations(
        db,
        company_id=1,
        invoice_id=2,
        lock_rows=True,
    )


@pytest.mark.asyncio
async def test_active_fulfillment_guard_true():
    db = SimpleNamespace(
        execute=AsyncMock(
            return_value=ScalarResult(
                123
            )
        )
    )

    assert await has_active_fulfillment_allocations(
        db,
        company_id=1,
        fulfillment_id=2,
        lock_rows=True,
    )


@pytest.mark.asyncio
async def test_active_fulfillment_guard_false():
    db = SimpleNamespace(
        execute=AsyncMock(
            return_value=ScalarResult(
                None
            )
        )
    )

    assert not await has_active_fulfillment_allocations(
        db,
        company_id=1,
        fulfillment_id=2,
        lock_rows=True,
    )


# STEP17B_AUTOUSE_FULFILLMENT_VAT_STUB
import pytest as _step17b_pytest
from unittest.mock import AsyncMock as _Step17BAsyncMock
import app.services.invoice_fulfillment_allocation_service as _step17b_fulfillment_service


@_step17b_pytest.fixture(autouse=True)
def _step17b_stub_fulfillment_vat_recognition(
    monkeypatch,
):
    stub = _Step17BAsyncMock(
        return_value=()
    )

    monkeypatch.setattr(
        _step17b_fulfillment_service,
        "reconcile_tax_for_invoice_line",
        stub,
    )

    return stub


# STEP17B_AUTOUSE_FULFILLMENT_SALES_RECOGNITION_STUB

@_step17b_pytest.fixture(autouse=True)
def _step17b_stub_fulfillment_sales_recognition(
    monkeypatch,
):
    stub = _Step17BAsyncMock(
        return_value=None
    )

    monkeypatch.setattr(
        _step17b_fulfillment_service,
        (
            "reconcile_sales_recognition_"
            "lifecycle_for_invoice_line"
        ),
        stub,
    )

    return stub

# INPUT_VAT_FULFILLMENT_BRIDGE_AUTOUSE_STUB
import pytest as _ivfb_pytest
from unittest.mock import AsyncMock as _IvfbAsyncMock
import app.services.invoice_fulfillment_allocation_service as _ivfb_allocation_service


@_ivfb_pytest.fixture(autouse=True)
def _stub_input_vat_fulfillment_bridge_lifecycle(
    monkeypatch,
):
    stub = _IvfbAsyncMock(
        return_value=()
    )

    monkeypatch.setattr(
        _ivfb_allocation_service,
        (
            "reconcile_input_vat_fulfillment_bridge_"
            "lifecycle_for_invoice_line"
        ),
        stub,
    )

    return stub
