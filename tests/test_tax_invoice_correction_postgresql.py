import importlib.util
import os
from datetime import date
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

import app.models  # noqa: F401

from app.api.v1.tax_invoices import (
    _create_output_with_initial_registration,
)
from app.core.config import settings
from app.core.database import Base
from app.models.idempotency_record import (
    IdempotencyRecord,
)
from app.models.idempotency_result import (
    IdempotencyResult,
)
from app.models.product_tax_classification import (
    ProductTaxClassification,
)
from app.models.tax_invoice_correction import (
    TaxInvoiceCorrection,
)
from app.models.tax_invoice_correction_line import (
    TaxInvoiceCorrectionLine,
)
from app.models.tax_invoice_correction_registration_event import (
    TaxInvoiceCorrectionRegistrationEvent,
)
from app.models.tax_recognition_event import (
    TaxRecognitionEvent,
)
from app.schemas.tax_invoice import (
    OutputTaxInvoiceCreate,
)
from app.services.tax_invoice_correction_orchestration_service import (
    TaxInvoiceCorrectionIdempotencyConflictError,
    append_tax_invoice_correction_registration_idempotent,
    create_tax_invoice_correction_idempotent,
)
from app.services.tax_invoice_correction_source_resolver_service import (
    TaxInvoiceCorrectionLineSource,
)
from app.services.tax_invoice_registration_lifecycle_service import (
    append_tax_invoice_registration_event,
)


RUN_POSTGRES_E2E = (
    os.getenv("RUN_POSTGRES_E2E") == "1"
)

pytestmark = pytest.mark.skipif(
    not RUN_POSTGRES_E2E,
    reason=(
        "Set RUN_POSTGRES_E2E=1 "
        "to run real PostgreSQL RK tests"
    ),
)


D1 = date(2026, 9, 1)
D2 = date(2026, 9, 2)
D3 = date(2026, 9, 3)


def D(value) -> Decimal:
    return Decimal(
        str(value)
    )


def load_vat_seed_module():
    path = Path(__file__).with_name(
        "test_vat_first_event_postgresql.py"
    )

    spec = importlib.util.spec_from_file_location(
        "_rk_vat_seed_helpers",
        path,
    )

    if (
        spec is None
        or spec.loader is None
    ):
        raise RuntimeError(
            "Cannot load VAT PostgreSQL seed helpers"
        )

    module = importlib.util.module_from_spec(
        spec
    )

    spec.loader.exec_module(
        module
    )

    return module


VAT_SEED = load_vat_seed_module()


async def count_rows(
    db: AsyncSession,
    model,
) -> int:
    return int(
        await db.scalar(
            select(
                func.count()
            ).select_from(
                model
            )
        )
        or 0
    )


@pytest.mark.asyncio
async def test_output_rk_idempotency_and_registration_real_postgresql():
    engine = create_async_engine(
        settings.database_url,
        poolclass=NullPool,
    )

    schema = (
        "test_rk_output_"
        + uuid4().hex
    )

    try:
        async with engine.connect() as conn:
            tx = await conn.begin()

            try:
                await conn.execute(
                    text(
                        f'CREATE SCHEMA "{schema}"'
                    )
                )

                await conn.execute(
                    text(
                        f'SET LOCAL search_path '
                        f'TO "{schema}"'
                    )
                )

                await conn.run_sync(
                    Base.metadata.create_all
                )

                async with AsyncSession(
                    bind=conn,
                    expire_on_commit=False,
                ) as db:
                    (
                        payment_allocation,
                        fulfillment_allocation,
                    ) = await VAT_SEED.seed(
                        db,
                        False,
                        True,
                    )

                    db.add_all(
                        [
                            payment_allocation,
                            fulfillment_allocation,
                        ]
                    )

                    await db.flush()

                    await db.execute(
                        text(
                            """
                            UPDATE companies
                            SET edrpou = '12345678',
                                vat_number = '111'
                            WHERE id = 1
                            """
                        )
                    )

                    await db.execute(
                        text(
                            """
                            UPDATE counterparties
                            SET tax_number = '87654321',
                                vat_number = '222'
                            WHERE id = 1
                            """
                        )
                    )

                    await db.execute(
                        text(
                            """
                            UPDATE products
                            SET base_uom_code = 'pcs'
                            WHERE id = 1
                            """
                        )
                    )

                    await db.execute(
                        text(
                            """
                            UPDATE trade_documents
                            SET vat_policy_id = 1,
                                counterparty_vat_registration_id = 1
                            WHERE id = 1
                            """
                        )
                    )

                    db.add(
                        ProductTaxClassification(
                            company_id=1,
                            product_id=1,
                            effective_from=D1,
                            classification_kind="uktzed",
                            statutory_code="1234567890",
                            created_by=1,
                        )
                    )

                    db.add(
                        TaxRecognitionEvent(
                            id=1,
                            company_id=1,
                            tax_calculation_id=1,
                            payment_settlement_allocation_id=1,
                            recognition_date=D1,
                            recognized_taxable_base=D("60"),
                            recognized_tax_amount=D("12"),
                            currency_code="UAH",
                            created_by=1,
                        )
                    )

                    await db.flush()

                    original_invoice = await (
                        _create_output_with_initial_registration(
                            db,
                            company_id=1,
                            payload=OutputTaxInvoiceCreate(
                                source_kind="settlement",
                                source_id=1,
                                document_number="PN-RK-BASE",
                            ),
                            created_by=1,
                        )
                    )

                    await (
                        append_tax_invoice_registration_event(
                            db,
                            company_id=1,
                            tax_invoice_id=(
                                original_invoice.id
                            ),
                            status="submitted",
                            event_date=D1,
                            reference="pn-submit",
                            created_by=1,
                        )
                    )

                    await (
                        append_tax_invoice_registration_event(
                            db,
                            company_id=1,
                            tax_invoice_id=(
                                original_invoice.id
                            ),
                            status="registered",
                            event_date=D2,
                            reference="pn-receipt",
                            created_by=1,
                        )
                    )

                    db.add(
                        TaxRecognitionEvent(
                            id=2,
                            company_id=1,
                            tax_calculation_id=1,
                            payment_settlement_allocation_id=1,
                            recognition_date=D3,
                            recognized_taxable_base=D("60"),
                            recognized_tax_amount=D("12"),
                            currency_code="UAH",
                            created_by=1,
                            reversal_of_id=1,
                        )
                    )

                    await db.flush()

                    sources = [
                        TaxInvoiceCorrectionLineSource(
                            line_number=1,
                            source_kind=(
                                "recognition_reversal"
                            ),
                            source_id=2,
                            reason_code=(
                                "advance_refund"
                            ),
                        )
                    ]

                    correction = await (
                        create_tax_invoice_correction_idempotent(
                            db,
                            company_id=1,
                            request_key="rk-create-1",
                            direction="output",
                            original_tax_invoice_id=(
                                original_invoice.id
                            ),
                            document_number="RK-1",
                            document_date=D3,
                            sources=sources,
                            created_by=1,
                        )
                    )

                    repeated = await (
                        create_tax_invoice_correction_idempotent(
                            db,
                            company_id=1,
                            request_key="rk-create-1",
                            direction="output",
                            original_tax_invoice_id=(
                                original_invoice.id
                            ),
                            document_number="RK-1",
                            document_date=D3,
                            sources=sources,
                            created_by=1,
                        )
                    )

                    assert (
                        repeated.id
                        == correction.id
                    )

                    assert (
                        await count_rows(
                            db,
                            TaxInvoiceCorrection,
                        )
                        == 1
                    )

                    assert (
                        await count_rows(
                            db,
                            TaxInvoiceCorrectionLine,
                        )
                        == 1
                    )

                    assert (
                        await count_rows(
                            db,
                            TaxInvoiceCorrectionRegistrationEvent,
                        )
                        == 1
                    )

                    line = await db.scalar(
                        select(
                            TaxInvoiceCorrectionLine
                        ).where(
                            TaxInvoiceCorrectionLine
                            .tax_invoice_correction_id
                            == correction.id
                        )
                    )

                    assert line is not None
                    assert (
                        line.source_kind
                        == "recognition_reversal"
                    )
                    assert (
                        line.tax_recognition_reversal_event_id
                        == 2
                    )
                    assert (
                        line.taxable_base_delta
                        == D("-60.00")
                    )
                    assert (
                        line.tax_amount_delta
                        == D("-12.00")
                    )
                    assert (
                        line.total_with_vat_delta
                        == D("-72.00")
                    )

                    history = list(
                        (
                            await db.scalars(
                                select(
                                    TaxInvoiceCorrectionRegistrationEvent
                                )
                                .where(
                                    TaxInvoiceCorrectionRegistrationEvent
                                    .tax_invoice_correction_id
                                    == correction.id
                                )
                                .order_by(
                                    TaxInvoiceCorrectionRegistrationEvent.id
                                )
                            )
                        ).all()
                    )

                    assert [
                        item.status
                        for item in history
                    ] == [
                        "prepared"
                    ]

                    with pytest.raises(
                        TaxInvoiceCorrectionIdempotencyConflictError
                    ):
                        await (
                            create_tax_invoice_correction_idempotent(
                                db,
                                company_id=1,
                                request_key="rk-create-1",
                                direction="output",
                                original_tax_invoice_id=(
                                    original_invoice.id
                                ),
                                document_number="RK-CONFLICT",
                                document_date=D3,
                                sources=sources,
                                created_by=1,
                            )
                        )

                    submitted = await (
                        append_tax_invoice_correction_registration_idempotent(
                            db,
                            company_id=1,
                            request_key="rk-submit-1",
                            tax_invoice_correction_id=(
                                correction.id
                            ),
                            status="submitted",
                            event_date=D3,
                            reference="rk-submit",
                            created_by=1,
                        )
                    )

                    registered = await (
                        append_tax_invoice_correction_registration_idempotent(
                            db,
                            company_id=1,
                            request_key="rk-register-1",
                            tax_invoice_correction_id=(
                                correction.id
                            ),
                            status="registered",
                            received_on=D3,
                            event_date=D3,
                            reference="rk-receipt",
                            created_by=1,
                        )
                    )

                    repeated_registration = await (
                        append_tax_invoice_correction_registration_idempotent(
                            db,
                            company_id=1,
                            request_key="rk-register-1",
                            tax_invoice_correction_id=(
                                correction.id
                            ),
                            status="registered",
                            received_on=D3,
                            event_date=D3,
                            reference="rk-receipt",
                            created_by=1,
                        )
                    )

                    assert (
                        repeated_registration.id
                        == registered.id
                    )

                    assert (
                        submitted.id
                        != registered.id
                    )

                    history = list(
                        (
                            await db.scalars(
                                select(
                                    TaxInvoiceCorrectionRegistrationEvent
                                )
                                .where(
                                    TaxInvoiceCorrectionRegistrationEvent
                                    .tax_invoice_correction_id
                                    == correction.id
                                )
                                .order_by(
                                    TaxInvoiceCorrectionRegistrationEvent.id
                                )
                            )
                        ).all()
                    )

                    assert [
                        item.status
                        for item in history
                    ] == [
                        "prepared",
                        "submitted",
                        "registered",
                    ]

                    assert (
                        await count_rows(
                            db,
                            IdempotencyRecord,
                        )
                        == 3
                    )

                    assert (
                        await count_rows(
                            db,
                            IdempotencyResult,
                        )
                        == 3
                    )

                    completed_statuses = list(
                        (
                            await db.scalars(
                                select(
                                    IdempotencyRecord.status
                                )
                                .order_by(
                                    IdempotencyRecord.id
                                )
                            )
                        ).all()
                    )

                    assert all(
                        str(item.value)
                        == "completed"
                        if hasattr(
                            item,
                            "value",
                        )
                        else str(item)
                        == "completed"
                        for item in completed_statuses
                    )

            finally:
                await tx.rollback()

    finally:
        await engine.dispose()
